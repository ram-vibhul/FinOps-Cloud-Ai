"""In-app notification centre (9.4): one row per person per notification."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from costlens.db.base import Base, Timestamps, UUIDPrimaryKey
from costlens.db.tenant import TenantMixin


class InboxItem(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "inbox_items"
    # A person sees their own notifications from every organisation they belong to.
    __tenant_user_column__ = "user_id"
    __table_args__ = (Index("ix_inbox_items_user_unread", "user_id", "read_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    event: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(String(2000), default="")
    link: Mapped[str | None] = mapped_column(String(500))
    severity: Mapped[str] = mapped_column(String(10), default="info")  # alert|resolved|info
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
