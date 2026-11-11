"""Virtual tag definitions."""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ccradar.db.base import Base, Timestamps, UUIDPrimaryKey
from ccradar.db.tenant import TenantMixin


class VirtualTag(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "virtual_tags"
    __audited__ = True
    __table_args__ = (UniqueConstraint("org_id", "key", name="uq_virtual_tags_key"),)

    key: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(String(500), default="")
    default_value: Mapped[str | None] = mapped_column(String(256))  # e.g. "unallocated"
    override_existing: Mapped[bool] = mapped_column(Boolean, default=False)
    rules: Mapped[list] = mapped_column(JSON, default=list)
