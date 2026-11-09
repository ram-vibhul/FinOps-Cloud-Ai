"""Daily snapshots of Savings Plans / Reserved Instance coverage, utilisation and AWS's
purchase recommendations (from Cost Explorer)."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from costlens.db.base import Base, Timestamps, UUIDPrimaryKey
from costlens.db.tenant import TenantMixin


class CommitmentSnapshot(TenantMixin, UUIDPrimaryKey, Base):
    __tablename__ = "commitment_snapshots"

    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("cloud_connections.id", ondelete="CASCADE"), index=True
    )
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class Commitment(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    """One Savings Plan or reservation as AWS describes it (refreshed daily)."""

    __tablename__ = "commitments"
    __table_args__ = (UniqueConstraint("org_id", "kind", "commitment_id"),)

    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("cloud_connections.id", ondelete="CASCADE")
    )
    # savings_plan | ec2_ri | rds_ri | elasticache_ri | opensearch_ri | redshift_ri
    kind: Mapped[str] = mapped_column(String(30))
    commitment_id: Mapped[str] = mapped_column(String(300))
    account_id: Mapped[str | None] = mapped_column(String(32))
    region: Mapped[str | None] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(String(300))  # plan type or instance type
    count: Mapped[int] = mapped_column(Integer, default=1)
    hourly_commitment: Mapped[float | None] = mapped_column(Float)  # Savings Plans
    upfront: Mapped[float | None] = mapped_column(Float)
    recurring_hourly: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    term_years: Mapped[float | None] = mapped_column(Float)
    payment_option: Mapped[str | None] = mapped_column(String(40))
    offering_class: Mapped[str | None] = mapped_column(String(20))  # standard | convertible
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(30))
    utilization_pct: Mapped[float | None] = mapped_column(Float)
    alerts_sent: Mapped[list] = mapped_column(JSON, default=list)  # e.g. [90, 30]
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Contract(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    """A negotiated agreement entered by hand (EDP / private pricing / marketplace)."""

    __tablename__ = "contracts"
    __audited__ = True

    name: Mapped[str] = mapped_column(String(200))
    provider: Mapped[str] = mapped_column(String(30), default="aws")
    kind: Mapped[str] = mapped_column(
        String(30), default="edp"
    )  # edp | private_pricing | marketplace | other
    commitment_amount: Mapped[float] = mapped_column(Float)  # total spend committed over the term
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    discount_pct: Mapped[float | None] = mapped_column(Float)
    starts_on: Mapped[date] = mapped_column(Date)
    ends_on: Mapped[date] = mapped_column(Date)
    notes: Mapped[str] = mapped_column(String(2000), default="")
    alerts_sent: Mapped[list] = mapped_column(JSON, default=list)


class PurchasePolicy(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    """Risk controls every planned commitment purchase must pass (one per organisation)."""

    __tablename__ = "purchase_policies"
    __table_args__ = (UniqueConstraint("org_id"),)
    __audited__ = True

    max_monthly_commitment: Mapped[float | None] = mapped_column(Float)  # new spend per month
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    max_term: Mapped[str] = mapped_column(String(3), default="1yr")  # 1yr | 3yr
    allowed_payments: Mapped[list] = mapped_column(
        JSON, default=lambda: ["no_upfront", "partial_upfront"]
    )
    max_coverage_pct: Mapped[float] = mapped_column(Float, default=80.0)  # of steady usage
    preview_days: Mapped[int] = mapped_column(Integer, default=7)


class PlannedPurchase(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    """A commitment someone plans to buy. CostLens never buys: people do, in the AWS
    console, after the preview and approval; CostLens then verifies the savings."""

    __tablename__ = "planned_purchases"
    __audited__ = True
    __audit_ignore__ = frozenset({"verification", "updated_at"})

    kind: Mapped[str] = mapped_column(String(30))  # savings_plan | reservation
    description: Mapped[str] = mapped_column(String(300))
    hourly_commitment: Mapped[float | None] = mapped_column(Float)
    instance_type: Mapped[str | None] = mapped_column(String(100))
    count: Mapped[int | None] = mapped_column(Integer)
    service: Mapped[str | None] = mapped_column(String(100))
    region: Mapped[str | None] = mapped_column(String(40))
    term: Mapped[str] = mapped_column(String(3), default="1yr")
    payment: Mapped[str] = mapped_column(String(20), default="no_upfront")
    monthly_cost: Mapped[float] = mapped_column(Float, default=0.0)  # what it commits per month
    estimated_monthly_saving: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    planned_for: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(
        String(20), default="planned"
    )  # planned|approved|cancelled|purchased
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    commitment_id: Mapped[str | None] = mapped_column(String(300))  # set when bought
    purchased_on: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str] = mapped_column(String(2000), default="")
    verification: Mapped[list] = mapped_column(
        JSON, default=list
    )  # [{month, saving, utilization_pct}]
