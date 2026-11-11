"""Append-only audit trail of changes inside an organisation."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from ccradar.db.base import Base, UUIDPrimaryKey, utcnow
from ccradar.db.tenant import TenantMixin


class AuditEvent(TenantMixin, UUIDPrimaryKey, Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_org_time", "org_id", "occurred_at"),)

    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    actor_type: Mapped[str] = mapped_column(String(20))  # user | system | api_key
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # no FK: survives deletion
    actor_label: Mapped[str] = mapped_column(String(320), default="")  # email at the time
    action: Mapped[str] = mapped_column(String(100), index=True)  # e.g. membership.updated
    target_type: Mapped[str] = mapped_column(String(100))
    target_id: Mapped[str | None] = mapped_column(String(100))
    summary: Mapped[str] = mapped_column(String(500), default="")
    changes: Mapped[dict] = mapped_column(JSON, default=dict)  # field -> [before, after]
    ip: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(500))
    request_id: Mapped[str | None] = mapped_column(String(64))
