"""Opt-in automatic fixes (8.6): one row per change CloudCostRadar is asked to make."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from ccradar.db.base import Base, Timestamps, UUIDPrimaryKey
from ccradar.db.tenant import TenantMixin


class FixAction(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "fix_actions"
    __audited__ = True

    finding_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("findings.id", ondelete="SET NULL"), index=True
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("cloud_connections.id", ondelete="CASCADE")
    )
    action: Mapped[str] = mapped_column(
        String(30)
    )  # stop_instance | delete_volume | release_address
    resource_id: Mapped[str] = mapped_column(String(200))
    region: Mapped[str] = mapped_column(String(40))
    savings_monthly: Mapped[float] = mapped_column(Float, default=0.0)
    commitment_cost: Mapped[float] = mapped_column(
        Float, default=0.0
    )  # money committed (purchases)
    # requested | approved | running | done | failed | rejected | cancelled
    status: Mapped[str] = mapped_column(String(10), default="requested")
    requested_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid
    )  # None: CloudCostRadar auto clean-up
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict] = mapped_column(JSON, default=dict)  # snapshot id, checks, error
