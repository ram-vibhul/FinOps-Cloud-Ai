"""Usage metering (B.12): per-day counters and daily snapshots per organisation."""

from __future__ import annotations

from datetime import date

from sqlalchemy import JSON, Date, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ccradar.db.base import Base, Timestamps, UUIDPrimaryKey
from ccradar.db.tenant import TenantMixin


class UsageCounter(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    """Things counted as they happen: ``api_calls``, ``ai_summaries``."""

    __tablename__ = "usage_counters"
    __table_args__ = (
        UniqueConstraint("org_id", "day", "metric", name="uq_usage_counters_org_day_metric"),
    )

    day: Mapped[date] = mapped_column(Date)
    metric: Mapped[str] = mapped_column(String(40))
    count: Mapped[int] = mapped_column(Integer, default=0)


class UsageSnapshot(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    """What an organisation used on one day (written by the nightly job)."""

    __tablename__ = "usage_snapshots"
    __table_args__ = (UniqueConstraint("org_id", "day", name="uq_usage_snapshots_org_day"),)

    day: Mapped[date] = mapped_column(Date, index=True)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
