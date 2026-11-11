"""Conservative, laddered commitment purchase plan.

Savings Plans: the steady On-Demand compute spend (EC2 instances, Fargate, Lambda
duration) is the chosen low percentile of daily spend over the window, minus the compute
spend pending CloudCostRadar changes will remove (4.17 ordering). The commitment is bought in
rungs spread over time, so expiries are staggered and usage changes can be followed;
plans that expire inside the horizon get a replacement rung.

Reservations (RDS, ElastiCache, OpenSearch, Redshift): per instance type and region, the
low percentile of instances running at once, excluding resources CloudCostRadar suggests
removing or resizing. Discounts are published-rate averages, not quotes: AWS's own
recommendation is used for Savings Plans when one exists."""

from __future__ import annotations

import math
import uuid
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ccradar.costs.fx import converted
from ccradar.db.base import Base
from ccradar.models import Commitment, Finding

HOURS = 730
MIN_DAYS = 14
SP_DISCOUNT = {  # Compute Savings Plans, typical published discount (estimate)
    ("1yr", "no_upfront"): 0.27,
    ("1yr", "partial_upfront"): 0.29,
    ("1yr", "all_upfront"): 0.31,
    ("3yr", "no_upfront"): 0.46,
    ("3yr", "partial_upfront"): 0.49,
    ("3yr", "all_upfront"): 0.52,
}
RI = {  # service -> (usage-type marker, typical 1-year no-upfront discount, 3-year factor)
    "Amazon Relational Database Service": ("instanceusage", 0.31),
    "Amazon ElastiCache": ("nodeusage", 0.30),
    "Amazon OpenSearch Service": ("esinstance", 0.31),
    "Amazon Redshift": ("node:", 0.24),
}
THREE_YEAR_FACTOR = 1.6  # 3-year terms save roughly 1.6x the 1-year discount
SP_USAGE = (
    ("%elastic compute cloud%", ("%boxusage%", "%dedicatedusage%")),
    ("%elastic container service%", ("%fargate%",)),
    ("%lambda%", ("%gb-second%", "%lambda-provisioned%")),
)
REMOVE_OR_RESIZE = ("remove", "size")


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct / 100
    lo, hi = math.floor(k), math.ceil(k)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def _window(db: Session, org_id: uuid.UUID, days: int) -> tuple[date, date] | None:
    t = Base.metadata.tables["cost_line_items"]
    first, last = db.execute(
        select(func.min(t.c.charge_date), func.max(t.c.charge_date)).where(
            t.c.org_id == org_id, t.c.provider == "aws"
        )
    ).one()
    if last is None:
        return None
    end = last - timedelta(days=1)  # the newest day is usually incomplete
    # Days before the bill starts are unknown, not zero usage.
    return max(end - timedelta(days=days - 1), first), end


def _not_committed(t: Any) -> Any:
    return or_(t.c.pricing_category.is_(None), t.c.pricing_category != "Committed")


def daily_sp_spend(
    db: Session, org_id: uuid.UUID, currency: str, start: date, end: date
) -> list[float]:
    """On-Demand spend per day that a Compute Savings Plan could cover."""
    t = Base.metadata.tables["cost_line_items"]
    amount = converted(t.c.billed_cost, t.c.billing_currency, t.c.charge_date, currency, t.c.org_id)
    eligible = or_(
        *[
            t.c.service_name.ilike(service) & or_(*[t.c.usage_type.ilike(u) for u in usages])
            for service, usages in SP_USAGE
        ]
    )
    totals = dict(
        db.execute(
            select(t.c.charge_date, func.sum(amount))
            .where(
                t.c.org_id == org_id,
                t.c.provider == "aws",
                t.c.charge_category == "Usage",
                _not_committed(t),
                eligible,
                ~t.c.usage_type.ilike("%spotusage%"),
                t.c.charge_date >= start,
                t.c.charge_date <= end,
            )
            .group_by(t.c.charge_date)
        ).all()
    )
    days = (end - start).days + 1
    return [float(totals.get(start + timedelta(days=i)) or 0) for i in range(days)]


def _pending(db: Session) -> tuple[float, set[str]]:
    """Monthly compute spend pending changes remove, and resources to keep out of RIs."""
    from ccradar.findings.pipeline import COMPUTE_TYPES, step_of

    removed: dict[str, float] = {}
    skip: set[str] = set()
    for f in db.scalars(select(Finding).where(Finding.status.in_(("open", "approved")))):
        step = step_of(f)
        if not step:
            continue
        if f.resource_type in COMPUTE_TYPES and float(f.savings_monthly) > 0:
            removed[f.overlap_key] = removed.get(f.overlap_key, 0.0) + float(f.savings_monthly)
        if step in REMOVE_OR_RESIZE and f.resource_id:
            skip.add(f.resource_id)
    return sum(removed.values()), skip


def _aws_discount(db: Session) -> float | None:
    from ccradar.findings.commitments import latest

    for snap, _ in latest(db):
        for rec in snap.data.get("sp_recommendations") or []:
            if rec.get("plan_type") == "COMPUTE_SP" and rec.get("savings_pct"):
                return float(rec["savings_pct"]) / 100
    return None


def savings_plan_plan(
    db: Session,
    org_id: uuid.UUID,
    currency: str,
    *,
    pct: float,
    term: str,
    payment: str,
    rungs: int,
    interval_months: int,
    days: int,
    today: date,
) -> dict[str, Any]:
    window = _window(db, org_id, days)
    if window is None:
        return {"status": "no_data", "reason": "No AWS bill imported yet."}
    spend = daily_sp_spend(db, org_id, currency, *window)
    active = [d for d in spend if d > 0]
    if len(active) < MIN_DAYS:
        return {
            "status": "no_data",
            "reason": f"Needs {MIN_DAYS} days of compute usage; {len(active)} found.",
        }
    pending_monthly, _ = _pending(db)
    steady_hourly = percentile(spend, pct) / 24
    after_hourly = max(steady_hourly - pending_monthly / HOURS, 0.0)
    aws = _aws_discount(db)
    discount = aws if aws is not None else SP_DISCOUNT[(term, payment)]
    total_commit = after_hourly * (1 - discount)
    rung = total_commit / rungs if rungs else 0.0
    ladder = []
    for i in range(rungs):
        when = today + timedelta(days=round(30.4 * interval_months * i))
        ladder.append(
            {
                "date": when.isoformat(),
                "hourly_commitment": round(rung, 2),
                "covers_on_demand_hourly": round(rung / (1 - discount), 2) if discount < 1 else 0,
                "monthly_saving": round(rung / (1 - discount) * discount * HOURS, 2)
                if discount < 1
                else 0,
                "reason": "new" if i else "first rung",
            }
        )
    horizon = today + timedelta(days=round(30.4 * interval_months * max(rungs, 1)))
    for c in db.scalars(
        select(Commitment).where(Commitment.kind == "savings_plan", Commitment.state == "active")
    ):
        if c.ends_at and today <= c.ends_at.date() <= horizon and c.hourly_commitment:
            ladder.append(
                {
                    "date": c.ends_at.date().isoformat(),
                    "hourly_commitment": round(c.hourly_commitment, 2),
                    "covers_on_demand_hourly": None,
                    "monthly_saving": None,
                    "reason": f"replace {c.description} plan ending then (if usage still needs it)",
                }
            )
    ladder.sort(key=lambda r: r["date"])
    return {
        "status": "ok",
        "window": {
            "start": window[0].isoformat(),
            "end": window[1].isoformat(),
            "days": len(spend),
        },
        "percentile": pct,
        "steady_on_demand_hourly": round(steady_hourly, 2),
        "pending_changes_monthly": round(pending_monthly, 2),
        "after_changes_on_demand_hourly": round(after_hourly, 2),
        "discount": round(discount, 3),
        "discount_source": "AWS recommendation" if aws is not None else "typical published rate",
        "total_hourly_commitment": round(total_commit, 2),
        "monthly_saving": round(after_hourly * discount * HOURS, 2),
        "ladder": ladder,
    }


def reservation_plan(
    db: Session, org_id: uuid.UUID, currency: str, *, pct: float, term: str, days: int
) -> list[dict[str, Any]]:
    window = _window(db, org_id, days)
    if window is None:
        return []
    start, end = window
    _, skip = _pending(db)
    t = Base.metadata.tables["cost_line_items"]
    amount = converted(t.c.billed_cost, t.c.billing_currency, t.c.charge_date, currency, t.c.org_id)
    out = []
    for service, (marker, discount_1y) in RI.items():
        rows = db.execute(
            select(t.c.region, t.c.usage_type, t.c.charge_date, t.c.resource_id,
                   func.sum(t.c.consumed_quantity), func.sum(amount))
            .where(
                t.c.org_id == org_id,
                t.c.service_name == service,
                t.c.charge_category == "Usage",
                _not_committed(t),
                t.c.usage_type.ilike(f"%{marker}%"),
                t.c.consumed_quantity.is_not(None),
                t.c.charge_date >= start,
                t.c.charge_date <= end,
            )
            .group_by(t.c.region, t.c.usage_type, t.c.charge_date, t.c.resource_id)
        ).all()  # fmt: skip
        hours: dict[tuple[str, str], dict[date, float]] = defaultdict(lambda: defaultdict(float))
        cost: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0.0, 0.0])
        for region, usage, day, rid, qty, money in rows:
            if rid and rid in skip:
                continue  # CloudCostRadar suggests removing or resizing it: do not reserve it
            key = (region or "", usage or "")
            hours[key][day] += float(qty or 0)
            cost[key][0] += float(money or 0)
            cost[key][1] += float(qty or 0)
        span = (end - start).days + 1
        for (region, usage), by_day in hours.items():
            series = [by_day.get(start + timedelta(days=i), 0.0) / 24 for i in range(span)]
            steady = math.floor(percentile(series, pct))
            spent, qty = cost[(region, usage)]
            if steady < 1 or qty <= 0:
                continue
            hourly_price = spent / qty
            discount = discount_1y * (THREE_YEAR_FACTOR if term == "3yr" else 1)
            out.append(
                {
                    "service": service,
                    "region": region,
                    "instance_type": usage.split(":", 1)[-1],
                    "count": steady,
                    "running_peak": math.ceil(max(series)),
                    "on_demand_hourly": round(hourly_price, 4),
                    "discount": round(discount, 3),
                    "monthly_saving": round(steady * hourly_price * HOURS * discount, 2),
                    "ladder": [
                        {"when": "now", "count": math.ceil(steady / 2)},
                        {"when": "in 3 months", "count": steady - math.ceil(steady / 2)},
                    ]
                    if steady > 1
                    else [{"when": "now", "count": steady}],
                }
            )
    out.sort(key=lambda r: -r["monthly_saving"])
    return out
