"""AI/LLM spend: what the cloud bills show for AI services, plus usage that customers'
applications report through the CloudCostRadar SDK (for providers billed outside the cloud
bill, such as the Anthropic and OpenAI APIs, and for per-app/team/feature detail).

Privacy (7.7): the ingest schema accepts counts and short identifiers only. Identifiers
may not contain spaces, so sentences cannot be smuggled in. The prompt is represented
only by a salted fingerprint (hex), made in the customer's process by the SDK; the salt
never leaves it. Unknown fields are rejected, not ignored."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from ccradar.db.base import utcnow
from ccradar.db.tenant import set_tenant
from ccradar.models import AiUsageEvent, AiUsageKey, CostDaily, Organization

PREFIX = "clai"
IDENT = r"^[\w.:/@+\-]{1,100}$"
MAX_BATCH = 1000
# USD per million tokens: (input, output, cache read, cache write). Prefix match on the
# model name, longest first. Please verify periodically (tracker 7.3); organisations can
# override any of them under Settings → AI usage.
PRICES: dict[str, tuple[float, float, float | None, float | None]] = {
    "claude-fable-5-1": (10.0, 50.0, 0.25, None),
    "claude-opus-5-5": (4.0, 20.0, 0.20, None),
    "claude-opus-5": (5.0, 25.0, None, None),
    "claude-opus-4": (5.0, 25.0, None, None),
    "claude-sonnet-5": (2.0, 10.0, 0.20, None),
    "claude-sonnet-4": (3.0, 15.0, None, None),
    "claude-haiku-5-5": (0.10, 0.50, None, None),
    "claude-haiku-4-5": (1.0, 5.0, None, None),
    "gpt-4.1-nano": (0.10, 0.40, None, None),
    "gpt-4.1-mini": (0.40, 1.60, None, None),
    "gpt-4.1": (2.0, 8.0, None, None),
    "gpt-4o-mini": (0.15, 0.60, None, None),
    "gpt-4o": (2.50, 10.0, None, None),
}
CACHE_READ_SHARE = 0.10  # of the input price, when the list has no cache-read price
CACHE_WRITE_SHARE = 1.25
# Cloud bill services that are AI spend (matched case-insensitively, by substring).
AI_SERVICES = (
    "bedrock",
    "sagemaker",
    "azure openai",
    "cognitive services",
    "foundry models",
    "vertex ai",
    "gemini",
    "generative ai",
)


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid")

    at: datetime
    provider: str = Field(pattern=IDENT, max_length=40)
    model: str = Field(pattern=IDENT)
    app: str | None = Field(default=None, pattern=IDENT)
    team: str | None = Field(default=None, pattern=IDENT)
    feature: str | None = Field(default=None, pattern=IDENT)
    environment: str | None = Field(default=None, pattern=IDENT, max_length=40)
    input_tokens: int = Field(default=0, ge=0, le=10_000_000)
    output_tokens: int = Field(default=0, ge=0, le=10_000_000)
    cache_read_tokens: int = Field(default=0, ge=0, le=10_000_000)
    cache_write_tokens: int = Field(default=0, ge=0, le=10_000_000)
    cost_usd: float | None = Field(default=None, ge=0, le=100_000)
    fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{16,64}$")
    retry: bool = False
    status: Literal["ok", "error"] = "ok"
    latency_ms: int | None = Field(default=None, ge=0, le=3_600_000)


class Batch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[Event] = Field(min_length=1, max_length=MAX_BATCH)


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def issue_key(db: Session, org_id: uuid.UUID, name: str) -> tuple[AiUsageKey, str]:
    secret = secrets.token_urlsafe(32)
    key = AiUsageKey(org_id=org_id, name=name, key_hash=_digest(secret), key_hint=secret[-6:])
    db.add(key)
    db.flush()
    return key, f"{PREFIX}_{secret}"


def authenticate(db: Session, header: str | None) -> AiUsageKey | None:
    token = (header or "").removeprefix("Bearer ").strip()
    if not token.startswith(f"{PREFIX}_"):
        return None
    digest = _digest(token.removeprefix(f"{PREFIX}_"))
    set_tenant(db, bypass=True)
    key = db.scalar(select(AiUsageKey).where(AiUsageKey.key_hash == digest))
    if key is None or key.revoked_at is not None or not hmac.compare_digest(key.key_hash, digest):
        return None
    set_tenant(db, org_id=key.org_id)
    return key


def price_list(
    org: Organization | None,
) -> dict[str, tuple[float, float, float | None, float | None]]:
    custom = ((org.settings if org else None) or {}).get("ai_prices") or {}
    out = dict(PRICES)
    for model, p in custom.items():
        out[model] = (
            float(p["input"]),
            float(p["output"]),
            float(p["cache_read"]) if p.get("cache_read") is not None else None,
            float(p["cache_write"]) if p.get("cache_write") is not None else None,
        )
    return out


def price(
    prices: dict[str, tuple[float, float, float | None, float | None]], model: str, e: Event
) -> float | None:
    # Bedrock IDs: "anthropic.claude-…", "us.anthropic.claude-…", or an inference-profile ARN.
    name = re.sub(r"^(us|eu|apac|global|us-gov|jp|au|ca)\.", "", model.lower().split("/")[-1])
    name = name.removeprefix("anthropic.")
    match = next((k for k in sorted(prices, key=len, reverse=True) if name.startswith(k)), None)
    if match is None:
        return None
    inp, out, cread, cwrite = prices[match]
    cread = inp * CACHE_READ_SHARE if cread is None else cread
    cwrite = inp * CACHE_WRITE_SHARE if cwrite is None else cwrite
    total = (
        e.input_tokens * inp
        + e.output_tokens * out
        + e.cache_read_tokens * cread
        + e.cache_write_tokens * cwrite
    )
    return round(total / 1_000_000, 6)


def ingest(db: Session, key: AiUsageKey, batch: Batch) -> dict[str, int]:
    org = db.get(Organization, key.org_id)
    prices = price_list(org)
    now = utcnow()
    unpriced = 0
    for e in batch.events:
        if e.cost_usd is not None:
            cost, source = e.cost_usd, "reported"
        else:
            p = price(prices, e.model, e)
            cost, source = (p, "price list") if p is not None else (0.0, "unknown")
            unpriced += p is None
        at = e.at if e.at.tzinfo else e.at.replace(tzinfo=now.tzinfo)
        db.add(
            AiUsageEvent(
                org_id=key.org_id,
                at=min(at, now + timedelta(minutes=5)),
                provider=e.provider.lower(),
                model=e.model,
                app=e.app,
                team=e.team,
                feature=e.feature,
                environment=e.environment,
                input_tokens=e.input_tokens,
                output_tokens=e.output_tokens,
                cache_read_tokens=e.cache_read_tokens,
                cache_write_tokens=e.cache_write_tokens,
                cost_usd=cost,
                cost_source=source,
                fingerprint=e.fingerprint,
                retry=e.retry,
                status=e.status,
                latency_ms=e.latency_ms,
            )
        )
    key.last_used_at = now
    db.flush()
    return {"accepted": len(batch.events), "unpriced": unpriced}


def _ai_service(name: str) -> bool:
    n = name.lower()
    return any(s in n for s in AI_SERVICES)


def summary(db: Session, org_id: uuid.UUID, start: date, end: date) -> dict[str, Any]:
    """AI spend from the bills (by service) and from the SDK (by model/app/team/feature)."""
    t = CostDaily
    bill_rows = db.execute(
        select(t.provider, t.service_name, t.billing_currency, func.sum(t.effective_cost))
        .where(
            t.org_id == org_id,
            t.charge_date >= start,
            t.charge_date <= end,
            or_(*[t.service_name.ilike(f"%{s}%") for s in AI_SERVICES]),
        )
        .group_by(t.provider, t.service_name, t.billing_currency)
    ).all()
    cloud = sorted(
        (
            {"provider": p, "service": s, "currency": c, "cost": round(float(v or 0), 2)}
            for p, s, c, v in bill_rows
            if _ai_service(s)
        ),
        key=lambda r: -r["cost"],
    )
    e = AiUsageEvent
    lo = datetime.combine(start, datetime.min.time(), tzinfo=utcnow().tzinfo)
    hi = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=utcnow().tzinfo)
    events = db.execute(
        select(
            e.provider, e.model, e.app, e.team, e.feature, e.cost_source,
            func.sum(e.cost_usd), func.count(), func.sum(e.input_tokens), func.sum(e.output_tokens),
            func.sum(e.cache_read_tokens), func.sum(e.cache_write_tokens),
            func.sum(case((e.retry.is_(True), 1), else_=0)),
        )
        .where(e.at >= lo, e.at < hi)
        .group_by(e.provider, e.model, e.app, e.team, e.feature, e.cost_source)
    ).all()  # fmt: skip
    by: dict[str, dict[str, dict[str, float]]] = {
        k: defaultdict(lambda: defaultdict(float)) for k in ("model", "app", "team", "feature")
    }
    totals: dict[str, float] = defaultdict(float)
    for provider, model, app, team, feature, source, cost, n, tin, tout, cr, cw, retries in events:
        keys = {
            "model": f"{provider}/{model}",
            "app": app or "(none)",
            "team": team or "(none)",
            "feature": feature or "(none)",
        }
        for dim, k in keys.items():
            row = by[dim][k]
            row["cost_usd"] += float(cost or 0)
            row["requests"] += int(n or 0)
            row["input_tokens"] += int(tin or 0)
            row["output_tokens"] += int(tout or 0)
        totals["cost_usd"] += float(cost or 0)
        totals["requests"] += int(n or 0)
        totals["input_tokens"] += int(tin or 0)
        totals["output_tokens"] += int(tout or 0)
        totals["cache_read_tokens"] += int(cr or 0)
        totals["cache_write_tokens"] += int(cw or 0)
        totals["retries"] += int(retries or 0)
        if source == "unknown":
            totals["unpriced_requests"] += int(n or 0)

    def ranked(rows: dict[str, dict[str, float]]) -> list[dict[str, Any]]:
        return sorted(
            (
                {"key": k, **{m: round(v, 6) if m == "cost_usd" else int(v) for m, v in r.items()}}
                for k, r in rows.items()
            ),
            key=lambda r: -r["cost_usd"],
        )

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "cloud_services": cloud,
        "sdk": {
            "totals": {k: (round(v, 4) if k == "cost_usd" else int(v)) for k, v in totals.items()},
            "by_model": ranked(by["model"]),
            "by_app": ranked(by["app"]),
            "by_team": ranked(by["team"]),
            "by_feature": ranked(by["feature"]),
        },
    }
