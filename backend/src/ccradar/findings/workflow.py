"""Finding workflow: open -> approved -> applied -> verified (or dismissed), with owners,
comments and history, and the realised saving measured from the bill."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ccradar.core.errors import AppError
from ccradar.costs.fx import converted
from ccradar.db.base import Base, utcnow
from ccradar.models import Finding, FindingEvent, K8sCost, Organization

MONTH = 30.4
BEFORE_DAYS = 30
MIN_AFTER_DAYS = 7
VERIFY_SHARE = 0.5  # at least half of the expected saving must show up
ALLOWED = {
    "open": {"approved", "applied", "dismissed"},
    "approved": {"applied", "open", "dismissed"},
    "applied": {"verified", "open"},
    "verified": {"open"},
    "dismissed": {"open"},
    "gone": {"open"},
}
ADMIN_ONLY = {"approved", "verified"}


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def event(
    db: Session,
    f: Finding,
    kind: str,
    user_id: uuid.UUID | None,
    text: str = "",
    data: dict[str, Any] | None = None,
) -> FindingEvent:
    e = FindingEvent(
        org_id=f.org_id,
        finding_id=f.id,
        at=utcnow(),
        user_id=user_id,
        kind=kind,
        text=text[:2000],
        data=data or {},
    )
    db.add(e)
    return e


def set_status(
    db: Session,
    f: Finding,
    new: str,
    user_id: uuid.UUID | None,
    *,
    is_admin: bool,
    note: str | None = None,
) -> None:
    if new == f.status:
        return
    if new not in ALLOWED.get(f.status, set()):
        raise AppError(
            f"A finding that is {f.status} cannot become {new}.", status=409, code="bad_transition"
        )
    if new in ADMIN_ONLY and not is_admin:
        raise AppError("Only admins can approve or verify savings.", status=403, code="admin_only")
    now, old = utcnow(), f.status
    f.status, f.status_note, f.status_by_id = new, note, user_id
    if new == "approved":
        f.approved_at = now
    elif new == "applied":
        f.applied_at, f.verified_at, f.realised_monthly = now, None, None
    elif new == "verified":
        f.verified_at = now
    elif new == "open":
        f.approved_at = f.applied_at = f.verified_at = None
    event(db, f, "status", user_id, note or "", {"from": old, "to": new})
    db.flush()


def set_owner(
    db: Session, f: Finding, owner_id: uuid.UUID | None, user_id: uuid.UUID | None
) -> None:
    f.owner_id = owner_id
    # A person's choice always wins over owner rules; clearing it hands back to the rules.
    f.owner_source = "person" if owner_id else None
    event(db, f, "owner", user_id, data={"owner_id": str(owner_id) if owner_id else None})
    db.flush()


def _daily_aws(db: Session, f: Finding, currency: str, start: datetime, end: datetime) -> dict:
    t = Base.metadata.tables["cost_line_items"]
    amount = converted(
        t.c.effective_cost, t.c.billing_currency, t.c.charge_date, currency, t.c.org_id
    )
    rows = db.execute(
        select(t.c.charge_date, func.sum(amount))
        .where(
            t.c.org_id == f.org_id,
            t.c.resource_id == f.resource_id,
            t.c.charge_date >= start.date(),
            t.c.charge_date < end.date(),
        )
        .group_by(t.c.charge_date)
    ).all()
    return {d: float(v or 0) for d, v in rows}


def _daily_k8s(db: Session, f: Finding, currency: str, start: datetime, end: datetime) -> dict:
    parts = (f.resource_id or "").split("/")
    if len(parts) != 4:
        return {}
    cluster, ns, kind, name = parts
    t = K8sCost
    amount = converted(t.cost, t.currency, func.date(t.hour), currency, t.org_id)
    day = func.date(t.hour)
    rows = db.execute(
        select(day, func.sum(amount))
        .where(
            t.cluster_id == uuid.UUID(cluster),
            t.namespace == ns,
            t.kind == kind,
            t.name == name,
            t.hour >= start,
            t.hour < end,
        )
        .group_by(day)
    ).all()
    return {str(d): float(v or 0) for d, v in rows}


def measure(db: Session, f: Finding, now: datetime | None = None) -> dict[str, Any] | None:
    """Average daily cost of the resource in the 30 days before the change vs. the days after
    (skipping the change day). None when there is nothing to measure."""
    if f.applied_at is None or not f.resource_id:
        return None
    if f.provider == "kubernetes":
        daily = _daily_k8s
    elif f.provider == "aws":
        daily = _daily_aws
    else:
        return None
    now = _aware(now or utcnow())
    applied = _aware(f.applied_at)
    org = db.get(Organization, f.org_id)
    currency = org.default_currency if org else f.currency
    before = daily(
        db, f, currency, applied - timedelta(days=BEFORE_DAYS), applied.replace(hour=0, minute=0)
    )
    after_start = (applied + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    after_end = now.replace(hour=0, minute=0, second=0, microsecond=0)  # whole days only
    after = daily(db, f, currency, after_start, after_end) if after_end > after_start else {}
    if not before:
        return None
    days_after = max((after_end - after_start).days, 0)
    before_avg = sum(before.values()) / len(before)
    after_avg = sum(after.values()) / max(days_after, 1) if days_after > 0 else None
    realised = None if after_avg is None else round((before_avg - after_avg) * MONTH, 2)
    return {
        "currency": currency,
        "before_daily": round(before_avg, 4),
        "after_daily": None if after_avg is None else round(after_avg, 4),
        "days_before": len(before),
        "days_after": max(days_after, 0),
        "realised_monthly": realised,
    }


def verify_applied(db: Session, now: datetime | None = None) -> dict[str, int]:
    """Measure every applied finding; verify those whose saving shows up in the bill."""
    counts = {"measured": 0, "verified": 0}
    for f in db.scalars(select(Finding).where(Finding.status == "applied")):
        m = measure(db, f, now)
        if m is None:
            continue
        f.measurement, f.measured_at = m, utcnow()
        f.realised_monthly = m["realised_monthly"]
        counts["measured"] += 1
        expected = float(f.savings_monthly)
        if (
            m["days_after"] >= MIN_AFTER_DAYS
            and m["realised_monthly"] is not None
            and expected > 0
            and m["realised_monthly"] >= VERIFY_SHARE * expected
        ):
            f.status, f.verified_at = "verified", utcnow()
            event(
                db,
                f,
                "status",
                None,
                "Saving confirmed in the bill.",
                {"from": "applied", "to": "verified", "realised_monthly": m["realised_monthly"]},
            )
            counts["verified"] += 1
    db.flush()
    return counts
