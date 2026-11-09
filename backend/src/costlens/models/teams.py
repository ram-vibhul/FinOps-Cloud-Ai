"""Teams inside an organisation, and email domains the organisation has proved it owns."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from costlens.db.base import Base, Timestamps, UUIDPrimaryKey
from costlens.db.tenant import TenantMixin


class Team(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "teams"
    __audited__ = True
    __table_args__ = (UniqueConstraint("org_id", "name"),)

    name: Mapped[str] = mapped_column(String(100))
    # The value of the cost "team" dimension this team owns (defaults to the name), so
    # findings, budgets and reports can be filtered to "my team".
    cost_key: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(String(500), default="")


class TeamMember(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "team_members"
    __audited__ = True
    __table_args__ = (UniqueConstraint("team_id", "user_id"),)

    team_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("teams.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )


class OrgDomain(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    """An email domain, verified by a DNS TXT record. A domain is verified for one
    organisation at a time (unique among verified domains)."""

    __tablename__ = "org_domains"
    __audited__ = True
    __audit_ignore__ = frozenset({"last_checked_at", "last_error"})
    __table_args__ = (UniqueConstraint("org_id", "domain"),)

    domain: Mapped[str] = mapped_column(String(253), index=True)
    token: Mapped[str] = mapped_column(String(64))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(300))
