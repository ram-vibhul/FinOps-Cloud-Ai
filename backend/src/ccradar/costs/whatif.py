"""What-if planner (11.39): "what would we pay if we…", on the organisation's own bill.

Each lever is a group of open findings (their savings already come from the customer's
bill and rates). A resource counts once across the chosen levers, as on the Savings page.
A Savings Plan is applied last, to the compute that is left after the other changes, so
the plan is never sized for servers the other levers remove."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ccradar.costs.analytics import latest_date
from ccradar.costs.fx import converted
from ccradar.db.base import Base
from ccradar.findings.service import _per_resource
from ccradar.models import Finding

HOURS = 730.0
GRAVITON = {"aws.graviton", "aws.lambda_arm64"}
LEVERS: dict[str, tuple[str, Any]] = {
    "graviton": ("Move to Graviton (Arm)", lambda f: f.detector in GRAVITON),
    "schedule": ("Stop non-production outside office hours", lambda f: f.category == "schedule"),
    "rightsizing": (
        "Right-size and modernise",
        lambda f: f.category == "rightsizing" and f.detector not in GRAVITON,
    ),
    "waste": ("Remove idle and unused resources", lambda f: f.category == "waste"),
    "other": (
        "Storage, database and network changes",
        lambda f: f.category in ("storage", "database", "network"),
    ),
}
COMPUTE = {"graviton", "schedule", "rightsizing"}  # levers that shrink Savings Plan usage


def base_monthly(db: Session, org_id: uuid.UUID, currency: str) -> float:
    """Spend of the last 30 billed days."""
    last = latest_date(db, org_id)
    if last is None:
        return 0.0
    daily = Base.metadata.tables["cost_daily"]
    amount = converted(
        daily.c.effective_cost, daily.c.billing_currency, daily.c.charge_date, currency,
        daily.c.org_id,
    )  # fmt: skip
    total = db.scalar(
        select(func.sum(amount)).where(
            daily.c.org_id == org_id,
            daily.c.charge_date > last - timedelta(days=30),
            daily.c.charge_date <= last,
        )
    )
    return float(total or 0)


def plan(
    db: Session,
    org_id: uuid.UUID,
    currency: str,
    levers: list[str],
    savings_plan: dict[str, Any] | None,
    today: date,
) -> dict[str, Any]:
    findings = [
        f
        for f in db.scalars(
            select(Finding).where(Finding.status == "open", Finding.savings_monthly > 0)
        )
        if f.currency == currency
    ]
    out_levers, chosen = [], []
    for key, (label, picks) in LEVERS.items():
        mine = [f for f in findings if picks(f)]
        monthly = sum(_per_resource(mine).values())
        out_levers.append(
            {
                "id": key,
                "label": label,
                "count": len(mine),
                "monthly": round(monthly, 2),
                "chosen": key in levers,
            }
        )
        if key in levers:
            chosen += mine
    combined = sum(_per_resource(chosen).values())  # each resource once
    compute_cut = sum(
        sum(_per_resource([f for f in findings if LEVERS[k][1](f)]).values())
        for k in COMPUTE
        if k in levers
    )
    commitment = None
    if savings_plan:
        from ccradar.costs.commitment_plan import savings_plan_plan

        sp = savings_plan_plan(
            db, org_id, currency, pct=float(savings_plan.get("percentile", 10)),
            term=str(savings_plan.get("term", "1yr")),
            payment=str(savings_plan.get("payment", "no_upfront")),
            rungs=1, interval_months=3, days=60, today=today,
        )  # fmt: skip
        if sp.get("status") == "ok":
            covered = float(sp["after_changes_on_demand_hourly"]) * HOURS
            left = max(covered - compute_cut, 0.0)
            saving = left * float(sp["discount"])
            commitment = {
                "term": savings_plan.get("term", "1yr"),
                "payment": savings_plan.get("payment", "no_upfront"),
                "discount": sp["discount"],
                "covers_monthly": round(left, 2),
                "monthly": round(saving, 2),
                "hourly_commitment": round(left * (1 - float(sp["discount"])) / HOURS, 2),
            }
        else:
            commitment = {"monthly": 0.0, "reason": sp.get("reason")}
    base = base_monthly(db, org_id, currency)
    total = combined + (commitment or {}).get("monthly", 0.0)
    return {
        "currency": currency,
        "base_monthly": round(base, 2),
        "levers": out_levers,
        "levers_monthly": round(combined, 2),
        "commitment": commitment,
        "total_monthly": round(total, 2),
        "after_monthly": round(max(base - total, 0.0), 2),
        "saving_pct": round(100 * total / base, 1) if base else None,
    }
