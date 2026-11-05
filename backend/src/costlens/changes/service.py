"""Record change events from any source and relate them to cost changes."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from costlens.db.base import utcnow
from costlens.models import ChangeEvent, CostAnomaly, CostDaily

Kind = Literal["deployment", "infrastructure", "config", "incident", "other"]


class ChangeIn(BaseModel):
    occurred_at: datetime | None = None
    kind: Kind = "deployment"
    title: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=2000)
    service: str | None = Field(default=None, max_length=200)
    account: str | None = Field(default=None, max_length=100)
    region: str | None = Field(default=None, max_length=60)
    resource_id: str | None = Field(default=None, max_length=1000)
    environment: str | None = Field(default=None, max_length=60)
    url: str | None = Field(default=None, max_length=1000, pattern=r"^https?://")
    actor: str | None = Field(default=None, max_length=320)
    external_id: str | None = Field(default=None, max_length=300)
    attributes: dict[str, Any] = Field(default_factory=dict)


def record(
    db: Session,
    org_id: uuid.UUID,
    change: ChangeIn,
    *,
    source: str,
    created_by: uuid.UUID | None = None,
) -> tuple[ChangeEvent, bool]:
    """Insert or update by (source, external_id). Returns (event, created)."""
    occurred = change.occurred_at or utcnow()
    if occurred.tzinfo is None:
        occurred = occurred.replace(tzinfo=UTC)
    external = (
        change.external_id
        or hashlib.sha256(
            f"{occurred.isoformat()}|{change.title}|{change.service}".encode()
        ).hexdigest()[:40]
    )
    existing = db.scalar(
        select(ChangeEvent).where(ChangeEvent.source == source, ChangeEvent.external_id == external)
    )
    values = change.model_dump(exclude={"occurred_at", "external_id"})
    if existing is not None:
        for key, value in values.items():
            setattr(existing, key, value)
        existing.occurred_at = occurred
        db.flush()
        return existing, False
    event = ChangeEvent(
        org_id=org_id,
        source=source,
        external_id=external,
        occurred_at=occurred,
        created_by_id=created_by,
        **values,
    )
    db.add(event)
    db.flush()
    return event, True


def _daily(
    db: Session, org_id: uuid.UUID, start: date, end: date, service: str | None, account: str | None
) -> dict[date, float]:
    stmt = (
        select(CostDaily.charge_date, func.sum(CostDaily.effective_cost))
        .where(
            CostDaily.org_id == org_id,
            CostDaily.charge_date >= start,
            CostDaily.charge_date <= end,
            CostDaily.charge_category.in_(("Usage", "Adjustment")),
        )
        .group_by(CostDaily.charge_date)
    )
    if service:
        stmt = stmt.where(CostDaily.service_name == service)
    if account:
        stmt = stmt.where(CostDaily.sub_account_id == account)
    return {d: float(v or 0) for d, v in db.execute(stmt).all()}


def cost_effect(
    db: Session, org_id: uuid.UUID, event: ChangeEvent, window: int = 7
) -> dict[str, Any] | None:
    """Average daily cost (of the event's service/account if known) after vs before."""
    day = event.occurred_at.date()
    series = _daily(
        db,
        org_id,
        day - timedelta(days=window),
        day + timedelta(days=window),
        event.service,
        event.account,
    )
    before = [series.get(day - timedelta(days=i), 0.0) for i in range(1, window + 1)]
    after_days = [day + timedelta(days=i) for i in range(1, window + 1)]
    available = [d for d in after_days if d in series]
    if not available or not any(before):
        return None
    after = [series[d] for d in available]
    b, a = sum(before) / len(before), sum(after) / len(after)
    return {
        "scope": event.service or event.account or "all spend",
        "before_per_day": round(b, 2),
        "after_per_day": round(a, 2),
        "change_per_day": round(a - b, 2),
        "change_pct": round((a - b) / b * 100, 1) if b else None,
        "days_after": len(available),
    }


def related_events(
    db: Session, anomaly: CostAnomaly, *, lookback_days: int = 3
) -> list[ChangeEvent]:
    """Changes that may explain an anomaly: same service/account (or unscoped), from a few
    days before the anomaly started until its last day."""
    start = datetime.combine(anomaly.day - timedelta(days=lookback_days), datetime.min.time(), UTC)
    end = datetime.combine(anomaly.last_day + timedelta(days=1), datetime.min.time(), UTC)
    events = db.scalars(
        select(ChangeEvent)
        .where(ChangeEvent.occurred_at >= start, ChangeEvent.occurred_at < end)
        .order_by(ChangeEvent.occurred_at.desc())
    ).all()

    def relevant(e: ChangeEvent) -> bool:
        if anomaly.dimension == "service":
            return e.service in (None, anomaly.key)
        if anomaly.dimension == "account":
            return e.account in (None, anomaly.key)
        return True

    return [e for e in events if relevant(e)][:5]
