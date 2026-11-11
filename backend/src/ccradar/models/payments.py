"""Payment provider webhook deliveries (B.5), kept to de-duplicate and to audit billing."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from ccradar.db.base import Base, Timestamps, UUIDPrimaryKey


class PaymentEvent(UUIDPrimaryKey, Timestamps, Base):
    """Platform-level (not tenant-scoped): a delivery arrives before we know the
    organisation. Holds no card or bank details, only ids, the event type and the outcome."""

    __tablename__ = "payment_events"
    __table_args__ = (UniqueConstraint("provider", "event_id", name="uq_payment_events_event"),)

    provider: Mapped[str] = mapped_column(String(20))  # razorpay | stripe
    event_id: Mapped[str] = mapped_column(String(200))
    event_type: Mapped[str] = mapped_column(String(100))
    org_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="SET NULL"), index=True
    )
    subscription_ref: Mapped[str | None] = mapped_column(String(200))
    outcome: Mapped[str] = mapped_column(String(40))  # applied | ignored | unknown_subscription …
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
