"""Risk controls for planned commitment purchases, and verification of what was bought.

CostLens never buys a commitment (cloud access is read-only). A planned purchase is
checked against the organisation's policy, previewed for at least ``preview_days``,
approved by an admin, bought by a person in the AWS console and marked purchased; the
savings are then measured from the bill month by month."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from costlens.costs import fx
from costlens.db.base import Base, utcnow
from costlens.models import Commitment, PlannedPurchase, PurchasePolicy

HOURS = 730
TERMS = {"1yr": 1, "3yr": 3}


def policy_for(db: Session, org_id: uuid.UUID) -> PurchasePolicy:
    p = db.scalar(select(PurchasePolicy))
    if p is None:
        p = PurchasePolicy(org_id=org_id, allowed_payments=["no_upfront", "partial_upfront"])
        db.add(p)
        db.flush()
    return p


def violations(
    db: Session,
    org_id: uuid.UUID,
    purchase: PlannedPurchase,
    *,
    steady_hourly: float | None,
    today: date | None = None,
) -> list[str]:
    """Everything that stops this purchase under the policy (empty: allowed)."""
    today = today or utcnow().date()
    p = policy_for(db, org_id)
    problems = []
    if TERMS.get(purchase.term, 99) > TERMS.get(p.max_term, 1):
        problems.append(
            f"The term {purchase.term} is longer than the policy allows ({p.max_term})."
        )
    if purchase.payment not in (p.allowed_payments or []):
        problems.append(
            f"Payment option {purchase.payment.replace('_', ' ')} is not allowed by the policy."
        )
    if purchase.planned_for < today + timedelta(days=p.preview_days):
        problems.append(
            f"Purchases must be planned at least {p.preview_days} days ahead for review."
        )
    if p.max_monthly_commitment is not None:
        rate = (
            1.0
            if p.currency == purchase.currency
            else fx.rate(db, today, purchase.currency, p.currency, org_id=org_id)
        )
        pending = sum(
            x.monthly_cost
            for x in db.scalars(
                select(PlannedPurchase).where(
                    PlannedPurchase.status.in_(("planned", "approved")),
                    PlannedPurchase.id != purchase.id,
                )
            )
        )
        total = (pending + purchase.monthly_cost) * float(rate or 1)
        if total > p.max_monthly_commitment:
            problems.append(
                f"New commitments would reach {total:,.2f} {p.currency} a month, over the limit of "
                f"{p.max_monthly_commitment:,.2f}."
            )
    if purchase.kind == "savings_plan" and steady_hourly and purchase.hourly_commitment:
        active = sum(
            c.hourly_commitment or 0
            for c in db.scalars(
                select(Commitment).where(
                    Commitment.kind == "savings_plan", Commitment.state == "active"
                )
            )
        )
        planned = sum(
            x.hourly_commitment or 0
            for x in db.scalars(
                select(PlannedPurchase).where(
                    PlannedPurchase.kind == "savings_plan",
                    PlannedPurchase.status.in_(("planned", "approved")),
                    PlannedPurchase.id != purchase.id,
                )
            )
        )
        share = 100 * (active + planned + purchase.hourly_commitment) / (steady_hourly + active)
        if share > p.max_coverage_pct:
            problems.append(
                f"Savings Plans would cover {share:.0f}% of steady usage; the policy allows "
                f"{p.max_coverage_pct:.0f}%."
            )
    return problems


def measured_saving(
    db: Session, org_id: uuid.UUID, commitment_id: str, currency: str, start: date, end: date
) -> float:
    """Saving on usage the commitment covered: list (On-Demand) cost minus effective cost."""
    t = Base.metadata.tables["cost_line_items"]
    listed = func.coalesce(t.c.list_cost, t.c.billed_cost)
    tail = commitment_id.rsplit("/", 1)[-1]
    diff = fx.converted(
        listed - t.c.effective_cost, t.c.billing_currency, t.c.charge_date, currency, t.c.org_id
    )
    total = db.scalar(
        select(func.sum(diff)).where(
            t.c.org_id == org_id,
            t.c.commitment_discount_id.ilike(f"%{tail}"),
            t.c.charge_date >= start,
            t.c.charge_date <= end,
        )
    )
    return round(float(total or 0), 2)


def verify(db: Session, org_id: uuid.UUID, currency: str, today: date | None = None) -> int:
    """Record last month's measured saving for every purchased plan (once per month)."""
    today = today or utcnow().date()
    month_end = today.replace(day=1) - timedelta(days=1)
    month_start = month_end.replace(day=1)
    label = month_start.strftime("%Y-%m")
    done = 0
    for x in db.scalars(select(PlannedPurchase).where(PlannedPurchase.status == "purchased")):
        if not x.commitment_id or (x.purchased_on and x.purchased_on > month_end):
            continue
        if any(v.get("month") == label for v in x.verification or []):
            continue
        start = max(month_start, x.purchased_on or month_start)
        saving = measured_saving(db, org_id, x.commitment_id, currency, start, month_end)
        c = db.scalar(
            select(Commitment).where(
                Commitment.commitment_id.ilike(f"%{x.commitment_id.rsplit('/', 1)[-1]}")
            )
        )
        x.verification = [
            *(x.verification or []),
            {
                "month": label,
                "saving": saving,
                "estimated": x.estimated_monthly_saving,
                "utilization_pct": c.utilization_pct if c else None,
                "currency": currency,
            },
        ]
        done += 1
    db.flush()
    return done


ADVICE = {
    "exchange": "Exchange it for a reservation that matches what you run now (convertible "
    "reservations can be exchanged at any time).",
    "modify_or_sell": "Modify it (size or Availability Zone within the family, Linux) to match "
    "running instances, or sell the remaining term on the Reserved Instance Marketplace.",
    "use_plan": "Savings Plans cannot be changed or sold: move eligible usage (any EC2 family, "
    "Fargate, Lambda) under it and buy less next time.",
    "use": "This reservation cannot be sold: run the matching instance type (size-flexible "
    "within the family where supported) or let it run out and do not renew.",
}


def advice_for(c: Commitment) -> dict[str, Any] | None:
    """What to do with an under-used commitment (it cannot be cancelled)."""
    if c.utilization_pct is None or c.utilization_pct >= 90:
        return None
    if c.kind == "ec2_ri":
        action = "exchange" if c.offering_class == "convertible" else "modify_or_sell"
    else:
        action = "use_plan" if c.kind == "savings_plan" else "use"
    return {"action": action, "text": ADVICE[action]}
