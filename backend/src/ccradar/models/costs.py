"""Canonical cost data, aligned with the FinOps Open Cost and Usage Specification (FOCUS).

* ``cloud_accounts`` - billing / linked accounts, subscriptions, projects.
* ``cost_import_runs`` - one row per import of a billing period from a source.
* ``cost_line_items`` - daily cost facts (FOCUS columns + provider extras). On
  PostgreSQL the table is range-partitioned by month on ``charge_date``.
* ``cost_daily`` - rollup by day and the common dimensions, rebuilt for the
  affected dates after every import; dashboards read this.

Money is ``Numeric(24, 10)``; every amount carries its billing currency.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from ccradar.db.base import Base, Timestamps, UUIDPrimaryKey
from ccradar.db.tenant import TenantMixin

Money = Numeric(24, 10)
Quantity = Numeric(28, 10)


class CloudAccount(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "cloud_accounts"
    __audited__ = True
    __table_args__ = (
        UniqueConstraint("org_id", "provider", "external_id", name="uq_cloud_accounts_external"),
    )

    provider: Mapped[str] = mapped_column(String(30))  # aws | azure | gcp | snowflake | ...
    external_id: Mapped[str] = mapped_column(String(100))  # e.g. 12-digit AWS account id
    name: Mapped[str] = mapped_column(String(200), default="")
    is_billing_account: Mapped[bool] = mapped_column(Boolean, default=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("cloud_accounts.id", ondelete="SET NULL")
    )
    environment: Mapped[str | None] = mapped_column(String(30))  # production | staging | ...
    tags: Mapped[dict] = mapped_column(JSON, default=dict)


class CostImportRun(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "cost_import_runs"

    source_kind: Mapped[str] = mapped_column(String(30))  # aws_cur | aws_ce | csv | focus | demo
    source_key: Mapped[str] = mapped_column(String(200))  # stable id of the source/connection
    billing_period: Mapped[date] = mapped_column(Date)  # first day of the month
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|succeeded|failed
    rows: Mapped[int] = mapped_column(Integer, default=0)
    total_cost: Mapped[Decimal | None] = mapped_column(Money)
    currency: Mapped[str | None] = mapped_column(String(3))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(String(1000))


class CostLineItem(TenantMixin, Base):
    """One day of one cost (FOCUS-aligned). Written in bulk with Core, never one by one."""

    __tablename__ = "cost_line_items"
    __table_args__ = (
        Index("ix_cost_line_items_org_date", "org_id", "charge_date"),
        Index("ix_cost_line_items_source", "org_id", "source_key", "billing_period"),
        Index("ix_cost_line_items_resource", "org_id", "resource_id"),
        {"postgresql_partition_by": "RANGE (charge_date)"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    charge_date: Mapped[date] = mapped_column(Date, primary_key=True)  # partition key
    billing_period: Mapped[date] = mapped_column(Date)
    source_key: Mapped[str] = mapped_column(String(200))
    import_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    provider: Mapped[str] = mapped_column(String(30))
    billing_account_id: Mapped[str | None] = mapped_column(String(100))
    sub_account_id: Mapped[str | None] = mapped_column(String(100))  # linked account / subscription
    sub_account_name: Mapped[str | None] = mapped_column(String(200))

    service_category: Mapped[str | None] = mapped_column(String(60))  # FOCUS ServiceCategory
    service_name: Mapped[str] = mapped_column(String(200))
    region: Mapped[str | None] = mapped_column(String(60))
    availability_zone: Mapped[str | None] = mapped_column(String(60))
    resource_id: Mapped[str | None] = mapped_column(String(1000))
    resource_name: Mapped[str | None] = mapped_column(String(500))
    resource_type: Mapped[str | None] = mapped_column(String(200))
    sku_id: Mapped[str | None] = mapped_column(String(200))
    usage_type: Mapped[str | None] = mapped_column(String(300))
    operation: Mapped[str | None] = mapped_column(String(200))

    charge_category: Mapped[str] = mapped_column(String(20))  # Usage|Purchase|Tax|Credit|Adjustment
    pricing_category: Mapped[str | None] = mapped_column(String(20))  # Standard|Committed|Dynamic
    commitment_discount_id: Mapped[str | None] = mapped_column(String(300))
    commitment_discount_type: Mapped[str | None] = mapped_column(String(40))

    consumed_quantity: Mapped[Decimal | None] = mapped_column(Quantity)
    consumed_unit: Mapped[str | None] = mapped_column(String(60))
    billed_cost: Mapped[Decimal] = mapped_column(Money)
    effective_cost: Mapped[Decimal] = mapped_column(Money)  # after discounts, amortised
    list_cost: Mapped[Decimal | None] = mapped_column(Money)
    billing_currency: Mapped[str] = mapped_column(String(3))

    tags: Mapped[dict] = mapped_column(JSON, default=dict)
    vtags: Mapped[dict] = mapped_column(JSON, default=dict)  # virtual tags (computed)
    extras: Mapped[dict] = mapped_column(JSON, default=dict)  # provider-specific columns


class CostDaily(TenantMixin, Base):
    """Daily rollup for fast dashboards and breakdowns."""

    __tablename__ = "cost_daily"
    __table_args__ = (Index("ix_cost_daily_org_date", "org_id", "charge_date"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    charge_date: Mapped[date] = mapped_column(Date)
    provider: Mapped[str] = mapped_column(String(30))
    sub_account_id: Mapped[str | None] = mapped_column(String(100))
    service_name: Mapped[str] = mapped_column(String(200))
    region: Mapped[str | None] = mapped_column(String(60))
    charge_category: Mapped[str] = mapped_column(String(20))
    pricing_category: Mapped[str | None] = mapped_column(String(20))
    billing_currency: Mapped[str] = mapped_column(String(3))
    billed_cost: Mapped[Decimal] = mapped_column(Money)
    effective_cost: Mapped[Decimal] = mapped_column(Money)
    list_cost: Mapped[Decimal | None] = mapped_column(Money)
    line_count: Mapped[int] = mapped_column(Integer)
