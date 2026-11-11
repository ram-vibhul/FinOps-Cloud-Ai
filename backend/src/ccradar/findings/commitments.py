"""Savings Plans / Reserved Instance findings from the latest Cost Explorer snapshot."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ccradar.costs import fx
from ccradar.db.base import utcnow
from ccradar.findings.registry import FindingIn, detector
from ccradar.models import CloudConnection, CommitmentSnapshot

UNDERUSED_PCT = 90.0
PLAN_NAMES = {"COMPUTE_SP": "Compute Savings Plan", "EC2_INSTANCE_SP": "EC2 Instance Savings Plan"}


def latest(db: Session) -> list[tuple[CommitmentSnapshot, str]]:
    """The newest snapshot per connection, with the connection's name."""
    newest = (
        select(CommitmentSnapshot.connection_id, func.max(CommitmentSnapshot.taken_at).label("at"))
        .group_by(CommitmentSnapshot.connection_id)
        .subquery()
    )
    rows = db.execute(
        select(CommitmentSnapshot, CloudConnection.name)
        .join(
            newest,
            CommitmentSnapshot.connection_id.is_not_distinct_from(newest.c.connection_id)
            & (CommitmentSnapshot.taken_at == newest.c.at),
        )
        .outerjoin(CloudConnection, CloudConnection.id == CommitmentSnapshot.connection_id)
    ).all()
    return [(snap, name or "AWS") for snap, name in rows]


class Converter:
    def __init__(self, db: Session, org_id: uuid.UUID, currency: str) -> None:
        self.db, self.org_id, self.currency, self.cache = db, org_id, currency, {}

    def __call__(self, amount: float, source: str) -> float | None:
        if source not in self.cache:
            r = fx.rate(self.db, utcnow().date(), source, self.currency, org_id=self.org_id)
            self.cache[source] = float(r) if r is not None else None
        rate = self.cache[source]
        return None if rate is None else round(amount * rate, 2)


@detector("aws.sp_purchase", category="commitments", title="Savings Plans to buy")
def sp_purchase(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    convert = Converter(db, org_id, currency)
    out = []
    for snap, account in latest(db):
        for rec in snap.data.get("sp_recommendations") or []:
            saving = convert(rec["monthly_savings"], rec.get("currency", "USD"))
            if not saving or saving < 1:
                continue
            plan = PLAN_NAMES.get(rec["plan_type"], rec["plan_type"])
            hourly = rec["hourly_commitment"]
            cur, pct = rec.get("currency", "USD"), rec["savings_pct"]
            out.append(
                FindingIn(
                    key=f"{snap.connection_id}:{rec['plan_type']}",
                    title=f"Buy a {plan} of {hourly:.2f} {cur}/hour ({account})",
                    description=(
                        f"AWS estimates {pct:.0f}% off the usage it would cover, based on "
                        f"the last 30 days ({rec['term']}, {rec['payment']}). It is a "
                        "commitment: you pay for the "
                        "hourly amount whether you use it or not, so size it to steady usage."
                    ),
                    savings_monthly=saving,
                    currency=currency,
                    risk="medium",
                    effort="low",
                    overlap=f"savings-plan:{snap.connection_id}",
                    provider="aws",
                    service="Savings Plans",
                    resource_type="savings_plan",
                    steps=[
                        "Check the recommendation in Cost Explorer → Savings Plans → "
                        "Recommendations.",
                        "Start smaller than recommended if usage is about to change (migrations, "
                        "right-sizing); add more later.",
                        "Purchase from the Savings Plans console (payer or the account itself).",
                    ],
                    evidence=rec,
                )
            )
    return out


@detector("aws.ri_purchase", category="commitments", title="Reserved Instances to buy")
def ri_purchase(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    convert = Converter(db, org_id, currency)
    out = []
    for snap, account in latest(db):
        for rec in snap.data.get("ri_recommendations") or []:
            saving = convert(rec["monthly_savings"], rec.get("currency", "USD"))
            if not saving or saving < 1:
                continue
            what = " ".join(x for x in (rec.get("engine"), rec["instance_type"]) if x)
            count = int(rec["count"]) if rec["count"] == int(rec["count"]) else rec["count"]
            short = rec["service"].replace("Amazon ", "")
            where, pct = rec.get("region") or "all regions", rec["savings_pct"]
            out.append(
                FindingIn(
                    key=f"{snap.connection_id}:{rec['service']}:{rec['instance_type']}:{rec.get('region')}",
                    title=f"Reserve {count} x {what} ({short}, {where}) for 1 year",
                    description=(
                        f"AWS estimates {pct:.0f}% off on-demand for this steady usage "
                        f"({rec['term']}, {rec['payment']}). Savings Plans do not cover {short}."
                    ),
                    savings_monthly=saving,
                    currency=currency,
                    risk="medium",
                    effort="low",
                    overlap=f"ri:{snap.connection_id}:{rec['service']}:{rec['instance_type']}:{rec.get('region')}",
                    provider="aws",
                    region=rec.get("region"),
                    service=rec["service"],
                    resource_type="reserved_instance",
                    steps=[
                        "Confirm the instances will keep running for a year (no planned resize).",
                        f"Purchase from the {short} console → Reserved instances.",
                    ],
                    evidence={**rec, "account": account},
                )
            )
    return out


@detector("aws.commitment_underused", category="commitments", title="Under-used commitments")
def commitment_underused(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    out = []
    for snap, account in latest(db):
        sp = snap.data.get("savings_plans") or {}
        if sp.get("total_commitment") and sp.get("utilization_pct", 100) < UNDERUSED_PCT:
            out.append(
                _underused(
                    snap,
                    account,
                    "Savings Plans",
                    sp["utilization_pct"],
                    currency,
                    {
                        "unused_commitment_30d": sp.get("unused_commitment"),
                        "currency_note": "Cost Explorer currency",
                    },
                )
            )
        ri = snap.data.get("reservations") or {}
        if ri.get("unused_hours") and ri.get("utilization_pct", 100) < UNDERUSED_PCT:
            out.append(
                _underused(
                    snap,
                    account,
                    "Reserved Instances",
                    ri["utilization_pct"],
                    currency,
                    {"unused_hours_30d": ri.get("unused_hours")},
                )
            )
    return out


def _underused(
    snap: CommitmentSnapshot,
    account: str,
    kind: str,
    pct: float,
    currency: str,
    evidence: dict[str, Any],
) -> FindingIn:
    return FindingIn(
        key=f"{snap.connection_id}:{kind}",
        title=f"{kind} only {pct:.0f}% used ({account})",
        description=(
            f"Part of what you already pay for {kind.lower()} went unused in the last 30 days. "
            "It cannot be cancelled, so there is no saving to claim here, but the unused part "
            "can be put to work."
        ),
        savings_monthly=0.0,
        currency=currency,
        risk="low",
        effort="medium",
        overlap=f"underused:{snap.connection_id}:{kind}",
        provider="aws",
        service=kind,
        steps=[
            "Move eligible workloads onto what the commitment covers (same region/family for "
            "instance-specific ones).",
            "Do not renew at the same size; check the expiry dates.",
            "Lower the next purchase recommendation accordingly.",
        ],
        evidence={**evidence, "utilization_pct": pct},
    )


@detector(
    "aws.commitment_actions",
    category="commitments",
    title="Under-used reservations to exchange, modify or sell",
)
def commitment_actions(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    """Per commitment: the unused share of what it costs, and what can be done about it."""
    from sqlalchemy import select

    from ccradar.costs.purchases import advice_for
    from ccradar.models import Commitment

    convert = Converter(db, org_id, currency)
    out = []
    for c in db.scalars(select(Commitment).where(Commitment.state == "active")):
        advice = advice_for(c)
        if advice is None:
            continue
        hourly = (c.hourly_commitment or 0) + (c.recurring_hourly or 0) * (c.count or 1)
        upfront_monthly = (c.upfront or 0) / max((c.term_years or 1) * 12, 1)
        monthly = convert(hourly * 730 + upfront_monthly, c.currency) or 0.0
        unused = monthly * (1 - (c.utilization_pct or 0) / 100)
        recoverable = advice["action"] in ("exchange", "modify_or_sell")
        out.append(
            FindingIn(
                key=c.commitment_id,
                title=f"{c.description} commitment only {c.utilization_pct:.0f}% used",
                description=(
                    f"About {unused:,.2f} {currency} a month of it goes unused. " + advice["text"]
                ),
                savings_monthly=round(unused, 2) if recoverable else 0.0,
                currency=currency,
                risk="medium",
                effort="medium",
                overlap=f"commitment:{c.commitment_id}",
                provider="aws",
                account_id=c.account_id,
                region=c.region,
                service="Commitments",
                resource_type=c.kind,
                resource_id=c.commitment_id,
                steps=[advice["text"]],
                evidence={
                    "utilization_pct": c.utilization_pct,
                    "unused_monthly": round(unused, 2),
                    "action": advice["action"],
                },
            )
        )
    return out
