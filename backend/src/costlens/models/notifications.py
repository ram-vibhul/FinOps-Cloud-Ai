"""Where an organisation's alerts go (Slack, Microsoft Teams). One place that later
also feeds the in-app notification centre (9.4)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from costlens.core.crypto import EncryptedString
from costlens.db.base import Base, Timestamps, UUIDPrimaryKey
from costlens.db.tenant import TenantMixin


class NotificationChannel(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "notification_channels"
    __audited__ = True
    __audit_redact__ = frozenset({"url"})
    __audit_ignore__ = frozenset({"last_sent_at", "last_error"})

    kind: Mapped[str] = mapped_column(String(20))  # slack | teams
    name: Mapped[str] = mapped_column(String(100))
    url: Mapped[str] = mapped_column(EncryptedString(2000))  # the webhook is a secret
    events: Mapped[list] = mapped_column(JSON, default=list)  # e.g. ["k8s_incident"]
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))
