"""What an organisation uses now, for plan limits (B.1). Daily metering snapshots and the
counters that need them (API calls, AI summaries) come with B.12."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from costlens.billing.plans import check_limit
from costlens.config import Settings
from costlens.db.base import utcnow
from costlens.models import (
    CloudConnection,
    CostDaily,
    Invitation,
    K8sCluster,
    Membership,
    Organization,
)


def _count(db: Session, stmt: Any) -> int:
    return int(db.scalar(stmt) or 0)


def members(db: Session) -> float:
    """People in the organisation plus invitations still open (they hold a seat)."""
    now = utcnow()
    pending = _count(
        db,
        select(func.count())
        .select_from(Invitation)
        .where(
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
            Invitation.expires_at > now,
        ),
    )
    return _count(db, select(func.count()).select_from(Membership)) + pending


def connections(db: Session) -> float:
    return _count(db, select(func.count()).select_from(CloudConnection))


def clusters(db: Session) -> float:
    return _count(db, select(func.count()).select_from(K8sCluster))


def cloud_spend_monthly_usd(db: Session) -> float:
    """Spend of the last 30 days in US dollars (days without an exchange rate are left out)."""
    from costlens.costs.fx import converted

    since = utcnow().date() - timedelta(days=30)
    amount = converted(
        CostDaily.effective_cost,
        CostDaily.billing_currency,
        CostDaily.charge_date,
        "USD",
        CostDaily.org_id,
    )
    total = db.scalar(select(func.sum(amount)).where(CostDaily.charge_date >= since))
    return round(float(total or 0), 2)


MEASURES: dict[str, Callable[[Session], float]] = {
    "members": members,
    "connections": connections,
    "clusters": clusters,
    "cloud_spend_monthly_usd": cloud_spend_monthly_usd,
}


def current_usage(db: Session) -> dict[str, float | None]:
    """Usage of the current organisation (tenant-scoped session) per limit; None = not measured."""
    from costlens.billing.metering import ai_summaries_this_month, api_calls_today
    from costlens.billing.plans import LIMITS

    counted = {
        "api_calls_daily": api_calls_today,
        "ai_summaries_monthly": ai_summaries_this_month,
    }
    measures = {**MEASURES, **counted}
    return {key: (measures[key](db) if key in measures else None) for key in LIMITS}


def enforce(db: Session, org: Organization, settings: Settings, key: str, adding: int = 1) -> None:
    """Refuse to add ``adding`` more of ``key`` when the plan's limit would be passed."""
    check_limit(org, settings, key, MEASURES[key](db), adding)
