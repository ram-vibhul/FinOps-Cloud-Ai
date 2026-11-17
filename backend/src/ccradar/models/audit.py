"""Append-only audit trail of changes inside an organisation."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from ccradar.core.crypto import EncryptedString
from ccradar.db.base import Base, Timestamps, UUIDPrimaryKey, utcnow
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


class AuditStream(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    """Streams the audit log to a customer's SIEM (11.42): Splunk HEC, Microsoft Sentinel,
    Datadog Logs, syslog over TLS or a signed webhook. A cursor over (occurred_at, id) means
    nothing is skipped or dropped: a failed send is retried with back-off from the same
    place."""

    __tablename__ = "audit_streams"
    __audited__ = True
    __audit_redact__ = frozenset({"secret", "signing_secret"})
    __audit_ignore__ = frozenset(
        {
            "cursor_at",
            "cursor_id",
            "last_sent_at",
            "last_status",
            "last_error",
            "failures",
            "next_attempt_at",
            "sent_total",
        }
    )

    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(
        String(20)
    )  # splunk_hec | sentinel | datadog | syslog | webhook
    settings: Mapped[dict] = mapped_column(JSON, default=dict)  # address, site, index, ...
    secret: Mapped[str | None] = mapped_column(EncryptedString(4000))  # token / key / secret
    signing_secret: Mapped[str] = mapped_column(EncryptedString(200))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    cursor_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cursor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failures: Mapped[int] = mapped_column(Integer, default=0)
    sent_total: Mapped[int] = mapped_column(Integer, default=0)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(String(500))
