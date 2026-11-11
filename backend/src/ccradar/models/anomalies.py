"""Detected cost anomalies (unexpected increases)."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from ccradar.db.base import Base, Timestamps, UUIDPrimaryKey
from ccradar.db.tenant import TenantMixin


class CostAnomaly(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "cost_anomalies"
    __audited__ = True
    __table_args__ = (
        UniqueConstraint(
            "org_id", "day", "dimension", "key", "currency", name="uq_cost_anomalies_day_key"
        ),
    )

    day: Mapped[date] = mapped_column(Date, index=True)  # first day of the episode
    last_day: Mapped[date] = mapped_column(Date)  # latest day still above normal
    days: Mapped[int] = mapped_column(Integer, default=1)
    dimension: Mapped[str] = mapped_column(String(30))  # total | service | account
    key: Mapped[str] = mapped_column(String(300))
    currency: Mapped[str] = mapped_column(String(3))
    actual: Mapped[float] = mapped_column(Float)
    expected: Mapped[float] = mapped_column(Float)
    upper: Mapped[float] = mapped_column(Float)  # top of the normal band
    impact: Mapped[float] = mapped_column(Float)  # extra spend over the whole episode
    peak_impact: Mapped[float] = mapped_column(Float, default=0.0)  # largest single-day extra
    score: Mapped[float] = mapped_column(Float)  # robust z-score
    status: Mapped[str] = mapped_column(String(20), default="open")
    # open | acknowledged | expected | resolved
    note: Mapped[str | None] = mapped_column(String(1000))
    status_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
