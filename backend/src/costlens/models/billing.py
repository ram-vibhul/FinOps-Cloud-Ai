"""Subscriptions (B.2): one per organisation, independent of the payment provider."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from costlens.db.base import Base, Timestamps, UUIDPrimaryKey
from costlens.db.tenant import TenantMixin


class Subscription(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "subscriptions"
    __audited__ = True
    __table_args__ = (UniqueConstraint("org_id", name="uq_subscriptions_org"),)

    plan_id: Mapped[str] = mapped_column(String(50))
    # trialing | active | past_due | ended
    status: Mapped[str] = mapped_column(String(12), default="trialing")
    interval: Mapped[str] = mapped_column(String(10), default="monthly")  # monthly | annual
    # manual (invoiced by Yugiverse staff) | razorpay | stripe (B.3, B.4)
    provider: Mapped[str] = mapped_column(String(20), default="manual")
    provider_ref: Mapped[str | None] = mapped_column(String(200))
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False)
    pending_plan_id: Mapped[str | None] = mapped_column(String(50))  # a downgrade, at period end
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Failed payments (B.16): when the subscription became past due, and how many
    # reminders have gone out since.
    past_due_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reminders_sent: Mapped[int] = mapped_column(Integer, default=0)
