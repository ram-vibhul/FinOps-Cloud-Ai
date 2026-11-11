"""Business metrics (orders, active users, GB served...) for cost per unit."""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import JSON, Date, Float, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from ccradar.db.base import Base, Timestamps, UUIDPrimaryKey
from ccradar.db.tenant import TenantMixin


class UnitMetric(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "unit_metrics"
    __audited__ = True

    name: Mapped[str] = mapped_column(String(200))
    unit: Mapped[str] = mapped_column(String(60))  # what one unit is, e.g. "order"
    description: Mapped[str] = mapped_column(String(1000), default="")
    filters: Mapped[dict] = mapped_column(
        JSON, default=dict
    )  # which cost counts (explorer filters)
    metric: Mapped[str] = mapped_column(String(10), default="effective")
    target_per_unit: Mapped[float | None] = mapped_column(Float)


class UnitMetricValue(TenantMixin, UUIDPrimaryKey, Base):
    __tablename__ = "unit_metric_values"
    __table_args__ = (UniqueConstraint("metric_id", "day"),)

    metric_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("unit_metrics.id", ondelete="CASCADE"), index=True
    )
    day: Mapped[date] = mapped_column(Date)
    value: Mapped[float] = mapped_column(Float)
