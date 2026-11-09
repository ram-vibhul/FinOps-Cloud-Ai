"""Connections to customer cloud accounts (credentials never stored for AWS: we assume
their role with an external ID)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from costlens.core.crypto import EncryptedString
from costlens.db.base import Base, Timestamps, UUIDPrimaryKey
from costlens.db.tenant import TenantMixin


class CloudConnection(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "cloud_connections"
    __audited__ = True
    __audit_redact__ = frozenset({"secret"})
    __table_args__ = (UniqueConstraint("external_id", name="uq_cloud_connections_external_id"),)

    provider: Mapped[str] = mapped_column(String(30))  # aws | azure
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|active|error
    external_id: Mapped[str] = mapped_column(String(100))  # shared with the customer's trust policy
    role_arn: Mapped[str | None] = mapped_column(String(600))
    account_id: Mapped[str | None] = mapped_column(String(64))  # AWS 12 digits, Azure GUID
    cloud_account_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("cloud_accounts.id", ondelete="SET NULL")
    )
    cur_bucket: Mapped[str | None] = mapped_column(String(255))
    cur_prefix: Mapped[str | None] = mapped_column(String(500))
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    # Azure: the service principal's client secret; Google Cloud: the service account key
    # (JSON). AWS needs none: we assume a role. Sized for the ciphertext of a key file.
    secret: Mapped[str | None] = mapped_column(EncryptedString(8000))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(1000))
