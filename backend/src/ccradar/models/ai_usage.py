"""AI/LLM usage reported by customers' applications through the CloudCostRadar SDK.

Events carry counts and short identifiers only (provider, model, app, team, feature,
token counts, a salted fingerprint of the prompt). Prompt and completion text never
reach CloudCostRadar: the ingest schema has no field that could hold it."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ccradar.db.base import Base, Timestamps, UUIDPrimaryKey
from ccradar.db.tenant import TenantMixin


class AiUsageKey(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    """A write-only key for the SDK (hashed; shown once)."""

    __tablename__ = "ai_usage_keys"
    __audited__ = True
    __audit_ignore__ = frozenset({"last_used_at"})

    name: Mapped[str] = mapped_column(String(100))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    key_hint: Mapped[str] = mapped_column(String(8))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AiUsageEvent(TenantMixin, UUIDPrimaryKey, Base):
    __tablename__ = "ai_usage_events"
    __table_args__ = (Index("ix_ai_usage_events_org_at", "org_id", "at"),)

    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    provider: Mapped[str] = mapped_column(String(40))  # anthropic | openai | bedrock | ...
    model: Mapped[str] = mapped_column(String(100))
    app: Mapped[str | None] = mapped_column(String(100))
    team: Mapped[str | None] = mapped_column(String(100))
    feature: Mapped[str | None] = mapped_column(String(100))
    environment: Mapped[str | None] = mapped_column(String(40))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    cost_source: Mapped[str] = mapped_column(String(10))  # reported | price list | unknown
    fingerprint: Mapped[str | None] = mapped_column(String(64))  # salted hash of the prompt
    retry: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(10), default="ok")  # ok | error
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    # "sdk", or "bedrock_logs:<connection id>" for rows read from Bedrock invocation logs
    source: Mapped[str] = mapped_column(String(80), default="sdk", index=True)
