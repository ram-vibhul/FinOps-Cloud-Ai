"""Shared-cost allocation rules for showback and chargeback."""

from __future__ import annotations

from sqlalchemy import JSON, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ccradar.db.base import Base, Timestamps, UUIDPrimaryKey
from ccradar.db.tenant import TenantMixin


class AllocationRule(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    """Costs matching ``filters`` are a shared pool, split across the values of
    ``dimension`` (e.g. ``tag:team``). The lowest ``position`` takes a cost first."""

    __tablename__ = "allocation_rules"
    __audited__ = True

    name: Mapped[str] = mapped_column(String(200))
    position: Mapped[int] = mapped_column(Integer, default=100)
    dimension: Mapped[str] = mapped_column(String(140))  # what the pool is split across
    filters: Mapped[dict] = mapped_column(JSON, default=dict)  # which costs are shared
    method: Mapped[str] = mapped_column(
        String(20), default="proportional"
    )  # proportional | even | fixed
    targets: Mapped[list] = mapped_column(
        JSON, default=list
    )  # values to split across (empty = all with direct cost)
    percents: Mapped[dict] = mapped_column(
        JSON, default=dict
    )  # fixed: value -> percent (sums to 100)
