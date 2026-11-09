"""Budgets: amounts per period over a slice of spend (filters), nested to any depth."""

from __future__ import annotations

import uuid

from sqlalchemy import JSON, Float, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from costlens.db.base import Base, Timestamps, UUIDPrimaryKey
from costlens.db.tenant import TenantMixin


class Budget(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "budgets"
    __audited__ = True
    __audit_ignore__ = frozenset({"alert_state", "updated_at"})

    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("budgets.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    amount: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    period: Mapped[str] = mapped_column(
        String(10), default="monthly"
    )  # monthly | quarterly | annual
    metric: Mapped[str] = mapped_column(String(10), default="effective")  # effective | billed
    filters: Mapped[dict] = mapped_column(
        JSON, default=dict
    )  # explorer filters; ANDed with the parent's
    thresholds: Mapped[list] = mapped_column(JSON, default=lambda: [80, 100])  # % of the amount
    alert_on_forecast: Mapped[bool] = mapped_column(default=True)
    recipients: Mapped[list] = mapped_column(JSON, default=list)  # email addresses
    owner_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    alert_state: Mapped[dict] = mapped_column(
        JSON, default=dict
    )  # {period key: ["actual:80", ...]}
