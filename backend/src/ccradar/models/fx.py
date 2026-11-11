"""Daily exchange rates (global reference data, not tenant-scoped)."""

from __future__ import annotations

from datetime import date

from sqlalchemy import Date, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from ccradar.db.base import Base


class ExchangeRate(Base):
    """Units of ``currency`` per 1 EUR on ``day``. Every calendar day is present
    (weekends and holidays carry the last published rate forward)."""

    __tablename__ = "exchange_rates"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    currency: Mapped[str] = mapped_column(String(3), primary_key=True)
    per_eur: Mapped[float] = mapped_column(Numeric(20, 10))
    source: Mapped[str] = mapped_column(String(20))  # ecb | carried | manual
