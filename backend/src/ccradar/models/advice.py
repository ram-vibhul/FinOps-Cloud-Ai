"""Recommendations imported from AWS (Cost Optimization Hub, Compute Optimizer, Trusted
Advisor), kept as fetched and merged with CloudCostRadar findings by resource and action."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from ccradar.db.base import Base, Timestamps, UUIDPrimaryKey
from ccradar.db.tenant import TenantMixin


class ExternalRecommendation(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "external_recommendations"
    __table_args__ = (UniqueConstraint("org_id", "source", "source_id"),)

    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("cloud_connections.id", ondelete="CASCADE")
    )
    source: Mapped[str] = mapped_column(
        String(40)
    )  # cost_optimization_hub | compute_optimizer | trusted_advisor
    source_id: Mapped[str] = mapped_column(String(500))
    account_id: Mapped[str | None] = mapped_column(String(64))  # AWS 12 digits, Azure GUID
    region: Mapped[str | None] = mapped_column(String(40))
    resource_id: Mapped[str | None] = mapped_column(String(1000), index=True)
    resource_type: Mapped[str | None] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(40))  # rightsize | stop | delete | graviton | ...
    title: Mapped[str] = mapped_column(String(500))
    savings_monthly: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
