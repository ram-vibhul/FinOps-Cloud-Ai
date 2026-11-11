"""Usage metering (B.12).

- **Counters** grow as things happen: ``api_calls`` (requests with an API key) and
  ``ai_summaries`` (each AI explanation or answer). One row per organisation, day and
  metric, incremented with an upsert.
- **Snapshots**: every night, what each organisation used the day before (people,
  connections, clusters, cloud spend under management, API calls, AI summaries).
  Customers see them; plan limits (B.1) and billing (B.2 to B.4) read them.

Counted limits refuse further use once reached: API calls answer 429 ``plan_limit`` for
the rest of the day; AI summaries fall back to plain wording (or refuse a question)."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ccradar.config import Settings
from ccradar.core.errors import AppError
from ccradar.db.base import utcnow
from ccradar.db.tenant import current_tenant, set_tenant
from ccradar.models import Organization, UsageCounter, UsageSnapshot

COUNTED = {"api_calls": "api_calls_daily", "ai_summaries": "ai_summaries_monthly"}


def bump(db: Session, org_id: uuid.UUID, metric: str, by: int = 1, day: date | None = None) -> int:
    """Add ``by`` to today's counter and return the new count."""
    day = day or utcnow().date()
    if db.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    now = utcnow()
    stmt = insert(UsageCounter).values(
        id=uuid.uuid4(),
        org_id=org_id,
        day=day,
        metric=metric,
        count=by,
        created_at=now,
        updated_at=now,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["org_id", "day", "metric"],
        set_={"count": UsageCounter.count + by, "updated_at": now},
    ).returning(UsageCounter.count)
    return int(db.execute(stmt).scalar_one())


def counted(db: Session, metric: str, since: date, until: date | None = None) -> int:
    """Sum of a counter for the current organisation between two days (inclusive)."""
    q = select(func.coalesce(func.sum(UsageCounter.count), 0)).where(
        UsageCounter.metric == metric, UsageCounter.day >= since
    )
    if until is not None:
        q = q.where(UsageCounter.day <= until)
    return int(db.scalar(q) or 0)


def api_calls_today(db: Session) -> int:
    return counted(db, "api_calls", utcnow().date())


def ai_summaries_this_month(db: Session) -> int:
    return counted(db, "ai_summaries", utcnow().date().replace(day=1))


def _limit(db: Session, settings: Settings, key: str) -> tuple[int | None, str]:
    from ccradar.billing.plans import plan_for

    org_id = current_tenant(db).org_id
    org = db.get(Organization, org_id) if org_id else None
    if org is None:
        return None, ""
    plan = plan_for(org, settings)
    return plan.limit(key), plan.name


def record_api_call(db: Session, settings: Settings, org_id: uuid.UUID) -> None:
    """Count one API-key request; refuse it when today's limit is used up."""
    used = bump(db, org_id, "api_calls")
    limit, plan = _limit(db, settings, "api_calls_daily")
    if limit is not None and used > limit:
        raise AppError(
            f"The {plan} plan allows {limit:,} API calls a day. The count starts again at "
            "midnight UTC.",
            status=429,
            code="plan_limit",
        )


def use_ai(db: Session, settings: Settings, *, strict: bool = False) -> bool:
    """Count one AI summary if the month's allowance has room. Returns False when it is
    used up (or raises with ``strict``, for an explicit question)."""
    org_id = current_tenant(db).org_id
    if org_id is None:
        return True
    limit, plan = _limit(db, settings, "ai_summaries_monthly")
    if limit is not None and ai_summaries_this_month(db) >= limit:
        if strict:
            raise AppError(
                f"The {plan} plan includes {limit:,} AI summaries a month; this month's are "
                "used up.",
                status=402,
                code="plan_limit",
            )
        return False
    bump(db, org_id, "ai_summaries")
    return True


def snapshot(db: Session, org: Organization, day: date) -> UsageSnapshot:
    """Write (or rewrite) the usage of ``org`` on ``day``. The tenant must be ``org``."""
    from ccradar.billing.usage import MEASURES

    metrics: dict[str, Any] = {key: measure(db) for key, measure in MEASURES.items()}
    metrics["api_calls"] = counted(db, "api_calls", day, day)
    metrics["ai_summaries"] = counted(db, "ai_summaries", day, day)
    row = db.scalar(select(UsageSnapshot).where(UsageSnapshot.day == day))
    if row is None:
        row = UsageSnapshot(org_id=org.id, day=day, metrics=metrics)
        db.add(row)
    else:
        row.metrics = metrics
    db.flush()
    return row


def snapshot_all(db: Session, day: date | None = None) -> int:
    """Nightly: yesterday's usage for every organisation (caller sets a bypass tenant)."""
    day = day or utcnow().date() - timedelta(days=1)
    orgs = db.scalars(select(Organization).where(Organization.is_demo.is_(False))).all()
    for org in orgs:
        set_tenant(db, org_id=org.id)
        snapshot(db, org, day)
    set_tenant(db, bypass=True)
    return len(orgs)


def history(db: Session, days: int) -> list[dict[str, Any]]:
    since = utcnow().date() - timedelta(days=days)
    rows = db.scalars(
        select(UsageSnapshot).where(UsageSnapshot.day >= since).order_by(UsageSnapshot.day)
    ).all()
    return [{"day": r.day.isoformat(), **(r.metrics or {})} for r in rows]
