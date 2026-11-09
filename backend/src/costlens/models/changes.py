"""Change events (deployments, infrastructure changes) and inbound webhook receivers."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from costlens.core.crypto import EncryptedString
from costlens.db.base import Base, Timestamps, UUIDPrimaryKey
from costlens.db.tenant import TenantMixin


class ChangeEvent(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "change_events"
    __table_args__ = (
        UniqueConstraint("org_id", "source", "external_id", name="uq_change_events_source_id"),
        Index("ix_change_events_org_time", "org_id", "occurred_at"),
    )

    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(30))  # api | github | cloudtrail | manual | ...
    kind: Mapped[str] = mapped_column(
        String(30)
    )  # deployment | infrastructure | config | incident | other
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(String(2000))
    service: Mapped[str | None] = mapped_column(String(200))  # cost service name, if known
    account: Mapped[str | None] = mapped_column(String(100))
    region: Mapped[str | None] = mapped_column(String(60))
    resource_id: Mapped[str | None] = mapped_column(String(1000))
    environment: Mapped[str | None] = mapped_column(String(60))
    url: Mapped[str | None] = mapped_column(String(1000))
    actor: Mapped[str | None] = mapped_column(String(320))
    external_id: Mapped[str] = mapped_column(String(300))
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class InboundWebhook(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    """A URL the customer gives to GitHub (or similar) to send us events."""

    __tablename__ = "inbound_webhooks"
    __audited__ = True
    __audit_redact__ = ("secret",)
    __audit_ignore__ = frozenset({"last_received_at"})

    kind: Mapped[str] = mapped_column(String(30))  # github | alertmanager | pagerduty | opsgenie
    name: Mapped[str] = mapped_column(String(120))
    secret: Mapped[str] = mapped_column(EncryptedString(1000))
    last_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Alert receivers start investigations on this cluster.
    cluster_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("k8s_clusters.id", ondelete="CASCADE")
    )
