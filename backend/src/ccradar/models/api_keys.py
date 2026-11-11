"""Organisation API keys for scripts and integrations (8.1). Hashed; shown once."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from ccradar.db.base import Base, Timestamps, UUIDPrimaryKey
from ccradar.db.tenant import TenantMixin


class OrgApiKey(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "org_api_keys"
    __audited__ = True
    __audit_ignore__ = frozenset({"last_used_at"})

    name: Mapped[str] = mapped_column(String(100))
    scope: Mapped[str] = mapped_column(String(10))  # read | write | admin
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    key_hint: Mapped[str] = mapped_column(String(8))
    # The key acts with the lower of its scope and this person's current role, and stops
    # working if they leave the organisation.
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE")
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 8.14: through MCP this key may also start Kubernetes investigations (its only write).
    can_investigate: Mapped[bool] = mapped_column(Boolean, default=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
