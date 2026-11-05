"""Organisation data exports."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from costlens.db.base import Base, Timestamps, UUIDPrimaryKey
from costlens.db.tenant import TenantMixin


class DataExport(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "data_exports"

    requested_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|ready|failed
    storage_key: Mapped[str | None] = mapped_column(String(300))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    error: Mapped[str | None] = mapped_column(String(500))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
