"""Peer benchmarks (11.40): how an organisation's cost habits compare with others'.

Opt-in only, "give to get": an organisation sees its peers only after it shares its
own figures. What is shared is four ratios for the last full month, never amounts,
names, accounts, resources or tags:

- **effective savings rate**: how far below list price the usage was billed
  (discounts, Savings Plans, reservations, credits on usage);
- **waste**: open findings' monthly savings as a share of monthly spend;
- **allocated**: the share of spend with a team (tag or virtual tag);
- **cost per vCPU-hour**: EC2 instance cost divided by vCPU-hours (AWS only).

Spend sets a size band (small, medium, large) but is not stored as an amount.
Percentiles are shown only from at least ``MIN_PEERS`` organisations, within the
organisation's size band when that band has enough of them, else across all. Demo
organisations are never included.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ccradar.costs import quality
from ccradar.costs.carbon import INSTANCE, vcpus
from ccradar.costs.fx import converted, rate
from ccradar.db.base import Base, utcnow
from ccradar.models import BenchmarkSample, Finding, Organization

MIN_PEERS = 5
BANDS = ((10_000, "small"), (100_000, "medium"))  # USD a month; above: large
METRICS = {
    "effective_savings_rate": {"label": "Effective savings rate", "unit": "%", "better": "higher"},
    "waste_pct": {"label": "Waste (open savings / spend)", "unit": "%", "better": "lower"},
    "allocated_pct": {"label": "Spend allocated to a team", "unit": "%", "better": "higher"},
    "vcpu_hour_cost": {"label": "EC2 cost per vCPU-hour", "unit": "USD", "better": "lower"},
}

# The demo organisation's made-up peer group (marked as a sample in the response).
DEMO_PEERS = {
    "effective_savings_rate": {"p25": 11.0, "median": 19.5, "p75": 27.0},
    "waste_pct": {"p25": 4.0, "median": 7.5, "p75": 12.0},
    "allocated_pct": {"p25": 62.0, "median": 78.0, "p75": 91.0},
    "vcpu_hour_cost": {"p25": 0.031, "median": 0.038, "p75": 0.046},
}


def last_full_month(today: date) -> tuple[date, date]:
    end = today.replace(day=1) - timedelta(days=1)
    return end.replace(day=1), end


def band(spend_usd: float) -> str:
    return next((name for limit, name in BANDS if spend_usd < limit), "large")


def opted_in(org: Organization | None) -> bool:
    return bool(((org.settings if org else None) or {}).get("benchmarks"))


def measure(db: Session, org_id: uuid.UUID, today: date) -> dict[str, Any]:
    """This organisation's ratios (and size band) for the last full month, in USD."""
    start, end = last_full_month(today)
    li = Base.metadata.tables["cost_line_items"]
    usd = converted(
        li.c.effective_cost, li.c.billing_currency, li.c.charge_date, "USD", li.c.org_id
    )
    usd_list = converted(
        li.c.list_cost, li.c.billing_currency, li.c.charge_date, "USD", li.c.org_id
    )
    where = (li.c.org_id == org_id, li.c.charge_date >= start, li.c.charge_date <= end)
    spend = float(db.scalar(select(func.sum(usd)).where(*where)) or 0)
    metrics: dict[str, float | None] = dict.fromkeys(METRICS)
    if spend <= 0:
        return {"month": start, "size_band": band(0), "spend": 0.0, "metrics": metrics}

    eff, lst = db.execute(
        select(func.sum(usd), func.sum(usd_list)).where(
            *where, li.c.charge_category == "Usage", li.c.list_cost.is_not(None), li.c.list_cost > 0
        )
    ).one()
    if lst and float(lst) > 0:
        metrics["effective_savings_rate"] = round(
            max(0.0, 100 * (1 - float(eff or 0) / float(lst))), 1
        )

    waste = 0.0
    for currency, amount in db.execute(
        select(Finding.currency, func.sum(Finding.savings_monthly))
        .where(Finding.org_id == org_id, Finding.status == "open")
        .group_by(Finding.currency)
    ):
        r = rate(db, end, str(currency), "USD", org_id)
        waste += float(amount or 0) * float(r) if r is not None else 0.0
    metrics["waste_pct"] = round(min(100 * waste / spend, 100.0), 1)

    q = quality.report(db, org_id, start, end, "USD")
    metrics["allocated_pct"] = round(100 - float(q["unallocated"]["pct"]), 1)

    hours = cost = 0.0
    for usage_type, qty, amount in db.execute(
        select(li.c.usage_type, func.sum(li.c.consumed_quantity), func.sum(usd))
        .where(*where, li.c.provider == "aws", li.c.charge_category == "Usage",
               li.c.usage_type.like("%BoxUsage%"))
        .group_by(li.c.usage_type)
    ):  # fmt: skip
        m = INSTANCE.search(str(usage_type or ""))
        n = vcpus(m[2]) if m else None
        if n and qty:
            hours += float(qty) * n
            cost += float(amount or 0)
    if hours > 0:
        metrics["vcpu_hour_cost"] = round(cost / hours, 4)
    return {"month": start, "size_band": band(spend), "spend": spend, "metrics": metrics}


def refresh(db: Session, org_id: uuid.UUID, today: date | None = None) -> BenchmarkSample | None:
    """Store (or remove) this organisation's sample. Call with the tenant set."""
    org = db.get(Organization, org_id)
    if org is None or org.is_demo or not opted_in(org):
        db.execute(delete(BenchmarkSample).where(BenchmarkSample.org_id == org_id))
        return None
    found = measure(db, org_id, today or utcnow().date())
    sample = db.get(BenchmarkSample, org_id) or BenchmarkSample(org_id=org_id)
    sample.month, sample.size_band = found["month"], found["size_band"]
    sample.metrics, sample.updated_at = found["metrics"], utcnow()
    db.add(sample)
    db.flush()
    return sample


def _percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    k = (len(ordered) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def peers(db: Session, month: date, size_band: str) -> dict[str, Any]:
    """Percentiles per metric from the samples of ``month`` (no organisation named)."""
    rows = db.execute(
        select(BenchmarkSample.size_band, BenchmarkSample.metrics).where(
            BenchmarkSample.month == month
        )
    ).all()
    same = [m for b, m in rows if b == size_band]
    group, scope = (same, "size band") if len(same) >= MIN_PEERS else ([m for _, m in rows], "all")
    out: dict[str, Any] = {}
    for key in METRICS:
        values = [float(m[key]) for m in group if (m or {}).get(key) is not None]
        out[key] = (
            {
                "n": len(values),
                "p25": round(_percentile(values, 0.25), 4),
                "median": round(_percentile(values, 0.5), 4),
                "p75": round(_percentile(values, 0.75), 4),
            }
            if len(values) >= MIN_PEERS
            else {"n": len(values)}
        )
    return {"scope": scope, "organisations": len(group), "metrics": out}


def view(db: Session, org_id: uuid.UUID, today: date | None = None) -> dict[str, Any]:
    org = db.get(Organization, org_id)
    today = today or utcnow().date()
    mine = measure(db, org_id, today)
    demo = bool(org and org.is_demo)
    shared = opted_in(org) and not demo
    if demo:  # illustrative peers, so the demo shows the comparison; never real samples
        peer_view = {
            "scope": "size band",
            "organisations": 23,
            "metrics": {k: {"n": 23, **v} for k, v in DEMO_PEERS.items()},
        }
    else:
        peer_view = peers(db, mine["month"], mine["size_band"]) if shared else None
    return {
        "opted_in": shared,
        "sample": demo,
        "month": mine["month"].isoformat(),
        "size_band": mine["size_band"],
        "min_peers": MIN_PEERS,
        "definitions": METRICS,
        "mine": mine["metrics"],
        "peers": peer_view,
    }
