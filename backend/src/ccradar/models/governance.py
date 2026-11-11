"""Governance policies (e.g. "every resource needs an owner tag") and their violations."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from ccradar.db.base import Base, Timestamps, UUIDPrimaryKey
from ccradar.db.tenant import TenantMixin


class Policy(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "governance_policies"
    __audited__ = True

    name: Mapped[str] = mapped_column(String(200))
    # required_tags | allowed_regions | denied_instance_types | k8s_requests
    kind: Mapped[str] = mapped_column(String(30))
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    resource_types: Mapped[list] = mapped_column(
        JSON, default=list
    )  # empty = every type the kind applies to
    severity: Mapped[str] = mapped_column(
        String(10), default="warning"
    )  # info | warning | critical
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class PolicyViolation(TenantMixin, UUIDPrimaryKey, Base):
    __tablename__ = "governance_violations"
    __table_args__ = (UniqueConstraint("policy_id", "resource_key"),)

    policy_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("governance_policies.id", ondelete="CASCADE"), index=True
    )
    resource_key: Mapped[str] = mapped_column(String(1000))  # resource id, or cluster/ns/kind/name
    resource_type: Mapped[str] = mapped_column(String(60))
    account_id: Mapped[str | None] = mapped_column(String(100))
    region: Mapped[str | None] = mapped_column(String(60))
    name: Mapped[str | None] = mapped_column(String(500))
    detail: Mapped[str] = mapped_column(String(1000))
    monthly_cost: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | resolved | exempt
    note: Mapped[str | None] = mapped_column(String(1000))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
