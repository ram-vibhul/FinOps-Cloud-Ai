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

import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy import event as sa_event
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
    org_id = current_tenant(db).org_id
    pending = unsaved_api_calls(org_id) if org_id else 0
    return counted(db, "api_calls", utcnow().date()) + pending


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


@dataclass
class _Batch:
    """API calls of one organisation on one day, counted in this process (11.14)."""

    saved: int = 0  # the committed count in the database when last read or written
    pending: int = 0  # counted here, not yet written
    inflight: int = 0  # written in a transaction that has not committed yet
    last_write: float = field(default_factory=time.monotonic)


_BATCHES: dict[tuple[uuid.UUID, date], _Batch] = {}
_LOCK = threading.Lock()
FLUSH_EVERY = 20  # calls
FLUSH_SECONDS = 30.0


WRITES_KEY = "ccradar.metering_writes"


def _write(db: Session, org_id: uuid.UUID, day: date, batch: _Batch) -> None:
    """Add this process's unsaved calls to the database. They are only taken off the
    in-memory count once the request's transaction commits; if it rolls back (a refused
    or failed request), they stay pending and go out with the next write."""
    batch.last_write = time.monotonic()
    if not batch.pending:
        return
    amount = batch.pending
    batch.pending, batch.inflight = 0, batch.inflight + amount
    total = bump(db, org_id, "api_calls", by=amount, day=day)
    db.info.setdefault(WRITES_KEY, []).append((batch, amount, total))


@sa_event.listens_for(Session, "after_commit")
def _writes_committed(db: Session) -> None:
    writes = db.info.pop(WRITES_KEY, None)
    if writes:
        with _LOCK:
            for batch, amount, total in writes:
                batch.inflight -= amount
                batch.saved = max(batch.saved, total)


@sa_event.listens_for(Session, "after_rollback")
def _writes_rolled_back(db: Session) -> None:
    writes = db.info.pop(WRITES_KEY, None)
    if writes:
        with _LOCK:
            for batch, amount, _ in writes:  # nothing was saved: back to pending
                batch.inflight -= amount
                batch.pending += amount


def record_api_call(db: Session, settings: Settings, org_id: uuid.UUID) -> None:
    """Count one API-key request; refuse it when today's limit is used up.

    Counts are kept in this process and written every ``FLUSH_EVERY`` calls or
    ``FLUSH_SECONDS`` seconds instead of on every request (11.14). The limit is checked
    against the saved count plus this process's unsaved calls; with several processes an
    organisation can pass its daily limit by at most ``FLUSH_EVERY`` calls per process."""
    day = utcnow().date()
    limit, plan = _limit(db, settings, "api_calls_daily")
    with _LOCK:
        for key in [k for k in _BATCHES if k[1] != day]:
            del _BATCHES[key]  # yesterday's batches were written by their last flush
        batch = _BATCHES.get((org_id, day))
        if batch is None:
            batch = _BATCHES[(org_id, day)] = _Batch(saved=counted(db, "api_calls", day, day))
        over = limit is not None and batch.saved + batch.inflight + batch.pending >= limit
        if not over:
            batch.pending += 1
            if batch.pending >= FLUSH_EVERY or time.monotonic() - batch.last_write >= FLUSH_SECONDS:
                _write(db, org_id, day, batch)
    if over:
        raise AppError(
            f"The {plan} plan allows {limit:,} API calls a day. The count starts again at "
            "midnight UTC.",
            status=429,
            code="plan_limit",
        )


def unsaved_api_calls(org_id: uuid.UUID) -> int:
    with _LOCK:
        batch = _BATCHES.get((org_id, utcnow().date()))
        return batch.pending if batch else 0


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
