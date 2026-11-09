"""Closed months (frozen statements) and provider invoices for reconciliation."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, Float, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from costlens.db.base import Base, Timestamps, UUIDPrimaryKey
from costlens.db.tenant import TenantMixin


class MonthClose(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "month_closes"
    __table_args__ = (UniqueConstraint("org_id", "month"),)
    __audited__ = True

    month: Mapped[date] = mapped_column(Date)  # first day of the month
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    note: Mapped[str] = mapped_column(String(1000), default="")
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)  # the statement as it was closed


class Invoice(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "invoices"
    __table_args__ = (UniqueConstraint("org_id", "provider", "invoice_id"),)
    __audited__ = True

    provider: Mapped[str] = mapped_column(String(30), default="aws")
    billing_account_id: Mapped[str | None] = mapped_column(String(100))
    month: Mapped[date] = mapped_column(Date)
    invoice_id: Mapped[str] = mapped_column(String(200))
    amount: Mapped[float] = mapped_column(Float)  # total including tax
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    source: Mapped[str] = mapped_column(String(20), default="manual")  # manual | aws
