"""Outgoing webhooks: CloudCostRadar events posted to a customer's HTTPS endpoint, signed with
HMAC-SHA256 and retried with back-off."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from ccradar.core.crypto import EncryptedString
from ccradar.db.base import Base, Timestamps, UUIDPrimaryKey
from ccradar.db.tenant import TenantMixin


class OutgoingWebhook(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "outgoing_webhooks"
    __audited__ = True
    __audit_redact__ = frozenset({"url", "secret"})
    __audit_ignore__ = frozenset({"last_sent_at", "last_status", "last_error"})

    name: Mapped[str] = mapped_column(String(100))
    url: Mapped[str] = mapped_column(EncryptedString(2000))  # may carry a token in the path
    secret: Mapped[str] = mapped_column(EncryptedString(200))
    events: Mapped[list] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(String(500))


class WebhookDelivery(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "webhook_deliveries"

    webhook_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("outgoing_webhooks.id", ondelete="CASCADE"), index=True
    )
    event: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending | sent | failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_status: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(String(500))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
