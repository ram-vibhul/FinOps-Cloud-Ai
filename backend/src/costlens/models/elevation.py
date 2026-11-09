"""Temporary elevated access (8.4): a higher role for a limited time, with a reason."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from costlens.db.base import Base, Timestamps, UUIDPrimaryKey
from costlens.db.tenant import TenantMixin


class ElevatedAccess(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "elevated_access"
    __audited__ = True

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(10))  # member | admin | owner
    reason: Mapped[str] = mapped_column(String(500))
    minutes: Mapped[int] = mapped_column(Integer)
    # requested | active | denied | revoked | expired
    status: Mapped[str] = mapped_column(String(10), default="requested")
    requested_by_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
