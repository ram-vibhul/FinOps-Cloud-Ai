"""Users, identities, organisations, memberships and sessions."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from costlens.accounts.roles import Role
from costlens.db.base import Base, Timestamps, UUIDPrimaryKey
from costlens.db.tenant import TenantMixin


class User(UUIDPrimaryKey, Timestamps, Base):
    """A person. Global (not tenant-scoped): one user can belong to many organisations."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True)  # always lower-case
    name: Mapped[str] = mapped_column(String(200), default="")
    avatar_url: Mapped[str | None] = mapped_column(String(1000))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Operator of this installation (self-hosted admin / Yugiverse staff on SaaS).
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    identities: Mapped[list[Identity]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    memberships: Mapped[list[Membership]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Identity(UUIDPrimaryKey, Timestamps, Base):
    """A login from an identity provider, e.g. ("google", "<sub>")."""

    __tablename__ = "identities"
    __table_args__ = (
        UniqueConstraint("provider", "subject", name="uq_identities_provider_subject"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(100))
    subject: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(320))

    user: Mapped[User] = relationship(back_populates="identities")


class Organization(UUIDPrimaryKey, Timestamps, Base):
    """A customer tenant. All customer data hangs off an organisation."""

    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(63), unique=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    default_currency: Mapped[str] = mapped_column(String(3), default="USD")
    # Set when deletion is requested; data is purged after the grace period (1.14).
    deletion_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    memberships: Mapped[list[Membership]] = relationship(
        back_populates="organization", cascade="all, delete-orphan", passive_deletes=True
    )


class Membership(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "memberships"
    __tenant_user_column__ = "user_id"  # users may read their own memberships everywhere
    __table_args__ = (UniqueConstraint("org_id", "user_id", name="uq_memberships_org_user"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[Role] = mapped_column(
        Enum(
            Role,
            name="role",
            native_enum=False,
            length=16,
            values_callable=lambda e: [m.value for m in e],
            create_constraint=True,
        ),
        default=Role.VIEWER,
    )

    user: Mapped[User] = relationship(back_populates="memberships")
    organization: Mapped[Organization] = relationship(back_populates="memberships")


class UserSession(UUIDPrimaryKey, Timestamps, Base):
    """A signed-in browser session. Only a SHA-256 hash of the token is stored."""

    __tablename__ = "sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    active_org_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="SET NULL")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(500))

    user: Mapped[User] = relationship()
