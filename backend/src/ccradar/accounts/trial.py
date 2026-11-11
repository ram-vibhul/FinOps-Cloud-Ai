"""Free trial countdown for new SaaS organisations (9.5).

The trial is a ``trialing`` subscription (B.2) of the catalogue's trial plan. Self-hosted
and local installs, demo organisations and partner clients have no trial."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session, object_session

from ccradar.config import Settings
from ccradar.db.base import utcnow
from ccradar.models import Organization

WARN_DAYS = 3


def start_trial(
    db: Session, org: Organization, settings: Settings, now: datetime | None = None
) -> None:
    from ccradar.billing.subscriptions import start_trial as start

    start(db, org, settings, now)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _trial_end(org: Organization) -> tuple[datetime | None, bool]:
    """(when the trial ends, whether it has already been ended)."""
    from ccradar.billing.subscriptions import get

    db = object_session(org)
    sub = get(db, org.id) if db is not None and org.id is not None else None
    if sub is not None:
        if sub.trial_ends_at is None or sub.status not in ("trialing", "ended"):
            return None, False
        if sub.status == "ended":
            early = sub.ended_at is not None and _aware(sub.ended_at) < _aware(sub.trial_ends_at)
            return (None, False) if early else (sub.trial_ends_at, True)  # early: no banner
        return sub.trial_ends_at, False
    raw = (org.settings or {}).get("trial_ends_at")  # set by hand (tests, support)
    return (datetime.fromisoformat(raw) if raw else None), False


def trial_status(org: Organization, now: datetime | None = None) -> dict[str, Any]:
    ends, over = _trial_end(org)
    if ends is None:
        return {"status": "none", "ends_at": None, "days_left": None}
    ends = _aware(ends)
    if over:
        return {"status": "ended", "ends_at": ends.isoformat(), "days_left": 0}
    left = ends - (now or utcnow())
    if left.total_seconds() <= 0:
        return {"status": "ended", "ends_at": ends.isoformat(), "days_left": 0}
    days = left.days + (1 if left.seconds else 0)  # "1 day left" until the very end
    return {
        "status": "ending" if days <= WARN_DAYS else "active",
        "ends_at": ends.isoformat(),
        "days_left": days,
    }
