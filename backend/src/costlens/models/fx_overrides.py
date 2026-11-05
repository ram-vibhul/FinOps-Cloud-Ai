"""Organisation exchange-rate overrides (e.g. a fixed finance rate per month)."""

from __future__ import annotations

from datetime import date

from sqlalchemy import Date, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from costlens.db.base import Base, Timestamps, UUIDPrimaryKey
from costlens.db.tenant import TenantMixin


class FxOverride(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    """``rate`` units of ``to_currency`` per 1 ``from_currency`` on every day in
    ``start_day..end_day``. Used before daily reference rates, in both directions."""

    __tablename__ = "fx_overrides"
    __audited__ = True
    __table_args__ = (
        UniqueConstraint(
            "org_id", "from_currency", "to_currency", "start_day", name="uq_fx_overrides_start"
        ),
    )

    from_currency: Mapped[str] = mapped_column(String(3))
    to_currency: Mapped[str] = mapped_column(String(3))
    start_day: Mapped[date] = mapped_column(Date)
    end_day: Mapped[date] = mapped_column(Date)
    rate: Mapped[float] = mapped_column(Numeric(20, 10))
    note: Mapped[str] = mapped_column(String(300), default="")
