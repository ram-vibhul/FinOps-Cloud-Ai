"""Instance-wide (not tenant-scoped) settings, e.g. licence key, demo seeded flag."""

from __future__ import annotations

from sqlalchemy import JSON, BigInteger, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from costlens.db.base import Base, Timestamps


class InstanceSetting(Timestamps, Base):
    __tablename__ = "instance_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)


class RateLimitBucket(Base):
    """Shared rate-limit counters (PostgreSQL backend). Rows expire via a periodic job."""

    __tablename__ = "rate_limit_buckets"

    bucket: Mapped[str] = mapped_column(String(200), primary_key=True)
    window_start: Mapped[int] = mapped_column(BigInteger)  # unix seconds
    count: Mapped[int] = mapped_column(Integer)
