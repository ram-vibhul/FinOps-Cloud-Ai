"""Custom roles (8.8): a named set of permissions, optionally limited to some cloud
accounts and teams."""

from __future__ import annotations

from sqlalchemy import JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from costlens.db.base import Base, Timestamps, UUIDPrimaryKey
from costlens.db.tenant import TenantMixin


class CustomRole(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "custom_roles"
    __audited__ = True
    __table_args__ = (UniqueConstraint("org_id", "name"),)

    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(500), default="")
    permissions: Mapped[list] = mapped_column(JSON, default=list)  # Permission values
    # Empty lists = no limit. Account ids (sub_account_id) and team cost keys.
    accounts: Mapped[list] = mapped_column(JSON, default=list)
    teams: Mapped[list] = mapped_column(JSON, default=list)
