"""AWS audit: a service-by-service checklist where every row ends in a clear state."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from costlens.db.base import Base, Timestamps, UUIDPrimaryKey
from costlens.db.tenant import TenantMixin


class AuditRun(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "audit_runs"

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|done|failed
    trigger: Mapped[str] = mapped_column(String(20), default="manual")  # manual|scheduled
    requested_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(String(1000))


class AuditResult(TenantMixin, UUIDPrimaryKey, Base):
    __tablename__ = "audit_results"

    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("audit_runs.id", ondelete="CASCADE"), index=True
    )
    check_id: Mapped[str] = mapped_column(String(80))
    area: Mapped[str] = mapped_column(String(30))
    service: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20))  # fine|saving|more_data|manual
    reason: Mapped[str] = mapped_column(String(2000))
    spend_monthly: Mapped[float] = mapped_column(Float, default=0.0)
    savings_monthly: Mapped[float] = mapped_column(Float, default=0.0)
    finding_count: Mapped[int] = mapped_column(Integer, default=0)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
