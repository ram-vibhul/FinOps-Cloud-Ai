"""Uploaded billing files (CSV or Parquet; FOCUS or any CSV with a column mapping)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from costlens.db.base import Base, Timestamps, UUIDPrimaryKey
from costlens.db.tenant import TenantMixin


class FileImport(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "file_imports"
    __audited__ = True

    filename: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str | None] = mapped_column(String(300))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    file_type: Mapped[str] = mapped_column(String(10))  # csv | parquet
    detected_format: Mapped[str] = mapped_column(String(20))  # focus | custom
    status: Mapped[str] = mapped_column(String(20), default="uploaded")
    # uploaded | queued | importing | imported | failed
    columns: Mapped[list] = mapped_column(JSON, default=list)
    mapping: Mapped[dict] = mapped_column(JSON, default=dict)
    options: Mapped[dict] = mapped_column(JSON, default=dict)  # provider, currency, date_format
    report: Mapped[dict] = mapped_column(JSON, default=dict)
    rows_imported: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
