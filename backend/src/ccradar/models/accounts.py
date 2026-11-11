"""Users, identities, organisations, memberships and sessions."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ccradar.accounts.roles import Role
from ccradar.core.crypto import EncryptedString
from ccradar.db.base import Base, Timestamps, UUIDPrimaryKey
from ccradar.db.tenant import TenantMixin


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
    __audited__ = True

    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(63), unique=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    default_currency: Mapped[str] = mapped_column(String(3), default="USD")
    # Organisation preferences (alerting thresholds etc.); see costs.anomalies.AlertSettings.
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    # Retention per kind of data, in days (see privacy.retention); missing = default.
    retention_days: Mapped[dict] = mapped_column(JSON, default=dict)
    # Set when deletion is requested; data is purged after the grace period (1.14).
    deletion_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Partner mode (8.5): a partner manages client organisations (partner_id = the partner).
    is_partner: Mapped[bool] = mapped_column(Boolean, default=False)
    partner_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="SET NULL"), index=True
    )

    memberships: Mapped[list[Membership]] = relationship(
        back_populates="organization", cascade="all, delete-orphan", passive_deletes=True
    )


class Membership(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "memberships"
    __audited__ = True
    __audit_ignore__ = frozenset({"notification_prefs"})  # personal choices
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
    # A custom role (8.8) replaces the built-in role's permissions when set.
    custom_role_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("custom_roles.id", ondelete="SET NULL")
    )
    # Personal notification choices in this organisation (9.4):
    # {"inbox_off": [event, ...], "email": [event, ...]}
    notification_prefs: Mapped[dict] = mapped_column(JSON, default=dict)

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


class OrgIdentityProvider(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    """An organisation's own single sign-on provider (Entra, Okta, any OIDC)."""

    __tablename__ = "org_identity_providers"
    __audited__ = True
    __audit_redact__ = ("client_secret",)

    kind: Mapped[str] = mapped_column(String(20))
    display_name: Mapped[str] = mapped_column(String(100))
    issuer: Mapped[str] = mapped_column(String(500))
    tenant: Mapped[str | None] = mapped_column(String(200))
    client_id: Mapped[str] = mapped_column(String(500))
    client_secret: Mapped[str] = mapped_column(EncryptedString(4000))
    trust_email: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    @property
    def provider_id(self) -> str:
        return f"org:{self.id}"

    def to_config(self):  # type: ignore[no-untyped-def]  # -> ProviderConfig
        from ccradar.auth.providers import ENTRA_MULTITENANT, ProviderConfig

        return ProviderConfig(
            id=self.provider_id,
            display_name=self.display_name,
            issuer=self.issuer,
            client_id=self.client_id,
            client_secret=self.client_secret,
            kind=self.kind,  # type: ignore[arg-type]
            trust_email=self.trust_email,
            entra_multitenant=self.kind == "entra"
            and (self.tenant or "").lower() in ENTRA_MULTITENANT,
            customer_supplied=True,
        )


class Invitation(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    """An emailed invitation to join an organisation. Only a hash of the token is stored."""

    __tablename__ = "invitations"
    __audited__ = True
    __audit_redact__ = ("token_hash",)

    email: Mapped[str] = mapped_column(String(320), index=True)
    role: Mapped[Role] = mapped_column(
        Enum(
            Role,
            name="invitation_role",
            native_enum=False,
            length=16,
            values_callable=lambda e: [m.value for m in e],
            create_constraint=True,
        )
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    invited_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    organization: Mapped[Organization] = relationship()
    invited_by: Mapped[User | None] = relationship(foreign_keys=[invited_by_id])
