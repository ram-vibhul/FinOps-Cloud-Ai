"""Cloud resources (inventory) and recent hourly costs."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ccradar.db.base import Base, Timestamps, UUIDPrimaryKey
from ccradar.db.tenant import TenantMixin


class CloudResource(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "cloud_resources"
    __table_args__ = (
        UniqueConstraint("org_id", "provider", "resource_id", name="uq_cloud_resources_id"),
        Index("ix_cloud_resources_org_type", "org_id", "resource_type"),
    )

    provider: Mapped[str] = mapped_column(String(30))
    account_id: Mapped[str | None] = mapped_column(String(100))
    region: Mapped[str | None] = mapped_column(String(60))
    service: Mapped[str] = mapped_column(String(200))  # matches the cost service name
    resource_type: Mapped[str] = mapped_column(String(60))  # ec2_instance, ebs_volume, ...
    resource_id: Mapped[str] = mapped_column(String(1000))  # as it appears in billing data
    name: Mapped[str | None] = mapped_column(String(500))
    state: Mapped[str | None] = mapped_column(String(60))
    owner: Mapped[str | None] = mapped_column(String(320))
    created_in_provider_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    tags: Mapped[dict] = mapped_column(JSON, default=dict)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)  # size, type, engine, ...


class CostHourly(TenantMixin, UUIDPrimaryKey, Base):
    """Hourly cost for the last few weeks (from hourly billing exports)."""

    __tablename__ = "cost_hourly"
    __table_args__ = (Index("ix_cost_hourly_org_hour", "org_id", "hour"),)

    hour: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_key: Mapped[str] = mapped_column(String(200))
    sub_account_id: Mapped[str | None] = mapped_column(String(100))
    service_name: Mapped[str] = mapped_column(String(200))
    billing_currency: Mapped[str] = mapped_column(String(3))
    effective_cost: Mapped[Decimal] = mapped_column(Numeric(24, 10))


class ResourceMetric(TenantMixin, UUIDPrimaryKey, Base):
    """Daily summary of one CloudWatch metric for one resource (from hourly points)."""

    __tablename__ = "resource_metrics"
    __table_args__ = (
        UniqueConstraint("org_id", "resource_id", "metric", "day", name="uq_resource_metrics_day"),
    )

    resource_id: Mapped[str] = mapped_column(String(1000))
    metric: Mapped[str] = mapped_column(
        String(60)
    )  # cpu_pct | mem_pct | free_memory_bytes | connections
    day: Mapped[date] = mapped_column(Date, index=True)
    avg: Mapped[float] = mapped_column(Float)
    max: Mapped[float] = mapped_column(Float)  # highest hourly maximum
    min: Mapped[float] = mapped_column(Float)  # lowest hourly minimum (free memory)
    p95: Mapped[float] = mapped_column(Float)  # 95th percentile of hourly maxima
    samples: Mapped[int] = mapped_column(Integer)


class NetworkAttribution(TenantMixin, UUIDPrimaryKey, Base):
    """Who causes network transfer cost: one row per category and owner, from sampled VPC
    Flow Logs, with the category's billed monthly cost shared out by bytes."""

    __tablename__ = "network_attributions"

    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    category: Mapped[str] = mapped_column(String(30))  # nat_processing | cross_az | ...
    owner_key: Mapped[str] = mapped_column(String(500))
    owner_label: Mapped[str] = mapped_column(String(500))
    owner_kind: Mapped[str] = mapped_column(String(30))  # workload | instance | service | ip
    team: Mapped[str | None] = mapped_column(String(200))
    bytes: Mapped[float] = mapped_column(Float, default=0.0)
    share_pct: Mapped[float] = mapped_column(Float, default=0.0)
    cost_monthly: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    details: Mapped[dict] = mapped_column(JSON, default=dict)
