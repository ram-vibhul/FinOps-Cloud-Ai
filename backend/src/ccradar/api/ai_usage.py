"""AI/LLM spend: SDK ingest keys, the ingest endpoint, and the spend summary."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from ccradar.accounts.roles import Permission
from ccradar.api.deps import SessionDep, require
from ccradar.auth.web import AuthContext
from ccradar.core.errors import AppError, NotFoundError
from ccradar.costs import ai_usage
from ccradar.costs.analytics import latest_date
from ccradar.db.base import utcnow
from ccradar.models import AiUsageKey, Organization

router = APIRouter(tags=["ai usage"])
Reader = Annotated[AuthContext, require(Permission.READ)]
Admin = Annotated[AuthContext, require(Permission.MANAGE_SETTINGS)]


class KeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class KeyOut(BaseModel):
    id: uuid.UUID
    name: str
    key_hint: str
    last_used_at: datetime | None
    created_at: datetime
    key: str | None = None  # only when created


class PriceIn(BaseModel):
    input: float = Field(ge=0, le=1000)
    output: float = Field(ge=0, le=1000)
    cache_read: float | None = Field(default=None, ge=0, le=1000)
    cache_write: float | None = Field(default=None, ge=0, le=1000)


def _key_out(k: AiUsageKey, secret: str | None = None) -> KeyOut:
    return KeyOut(
        id=k.id,
        name=k.name,
        key_hint=k.key_hint,
        last_used_at=k.last_used_at,
        created_at=k.created_at,
        key=secret,
    )


@router.get("/api/v1/org/ai-usage-keys", response_model=list[KeyOut])
def list_keys(auth: Admin, db: SessionDep) -> list[KeyOut]:
    rows = db.scalars(
        select(AiUsageKey).where(AiUsageKey.revoked_at.is_(None)).order_by(AiUsageKey.created_at)
    )
    return [_key_out(k) for k in rows]


@router.post("/api/v1/org/ai-usage-keys", response_model=KeyOut, status_code=201)
def create_key(body: KeyIn, auth: Admin, db: SessionDep) -> KeyOut:
    assert auth.org_id is not None  # noqa: S101
    key, secret = ai_usage.issue_key(db, auth.org_id, body.name)
    return _key_out(key, secret)


@router.delete("/api/v1/org/ai-usage-keys/{key_id}", status_code=204)
def revoke_key(key_id: uuid.UUID, auth: Admin, db: SessionDep) -> Response:
    key = db.get(AiUsageKey, key_id)
    if key is None or key.revoked_at is not None:
        raise NotFoundError("Key not found.")
    key.revoked_at = utcnow()
    db.flush()
    return Response(status_code=204)


@router.post("/api/v1/ingest/ai-usage", status_code=202)
def ingest(
    body: ai_usage.Batch,
    db: SessionDep,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, int]:
    """For the CloudCostRadar SDK: a batch of usage events (counts only, never prompt text)."""
    key = ai_usage.authenticate(db, authorization)
    if key is None:
        raise AppError("Missing or invalid AI usage key.", status=401, code="unauthorized")
    return ai_usage.ingest(db, key, body)


@router.get("/api/v1/ai/summary")
def summary(
    auth: Reader,
    db: SessionDep,
    days: Annotated[int, Query(ge=1, le=400)] = 30,
) -> dict[str, Any]:
    assert auth.org_id is not None  # noqa: S101
    end = max(latest_date(db, auth.org_id) or date.min, utcnow().date())
    return ai_usage.summary(db, auth.org_id, end - timedelta(days=days - 1), end)


@router.get("/api/v1/ai/prices")
def prices(auth: Reader, db: SessionDep) -> dict[str, Any]:
    assert auth.org_id is not None  # noqa: S101
    org = db.get(Organization, auth.org_id)
    custom = ((org.settings if org else None) or {}).get("ai_prices") or {}
    return {
        "per": "USD per million tokens",
        "models": [
            {
                "model": m,
                "input": p[0],
                "output": p[1],
                "cache_read": p[2],
                "cache_write": p[3],
                "custom": m in custom,
            }
            for m, p in sorted(ai_usage.price_list(org).items())
        ],
    }


@router.put("/api/v1/ai/prices/{model}")
def set_price(model: str, body: PriceIn, auth: Admin, db: SessionDep) -> dict[str, Any]:
    import re

    if not re.fullmatch(ai_usage.IDENT, model):
        raise AppError("Not a model name.", code="bad_model")
    assert auth.org_id is not None  # noqa: S101
    org = db.get(Organization, auth.org_id)
    assert org is not None  # noqa: S101
    custom = dict((org.settings or {}).get("ai_prices") or {})
    custom[model.lower()] = body.model_dump()
    org.settings = {**(org.settings or {}), "ai_prices": custom}
    db.flush()
    return {"model": model.lower(), **body.model_dump()}


@router.delete("/api/v1/ai/prices/{model}", status_code=204)
def reset_price(model: str, auth: Admin, db: SessionDep) -> Response:
    assert auth.org_id is not None  # noqa: S101
    org = db.get(Organization, auth.org_id)
    assert org is not None  # noqa: S101
    custom = dict((org.settings or {}).get("ai_prices") or {})
    custom.pop(model.lower(), None)
    org.settings = {**(org.settings or {}), "ai_prices": custom}
    db.flush()
    return Response(status_code=204)


@router.get("/api/v1/ai/changes")
def changes(
    auth: Reader, db: SessionDep, days: Annotated[int, Query(ge=1, le=90)] = 7
) -> dict[str, Any]:
    """Why AI spend changed: the last ``days`` days against the ``days`` before."""
    from ccradar.costs.ai_insights import explain

    assert auth.org_id is not None  # noqa: S101
    return explain(db, auth.org_id, days)


@router.get("/api/v1/ai/waste")
def waste(auth: Reader, db: SessionDep) -> list[dict[str, Any]]:
    """Token waste over the last 30 days, with monthly savings in USD."""
    from dataclasses import asdict

    from ccradar.costs.ai_insights import waste as find_waste

    assert auth.org_id is not None  # noqa: S101
    return [asdict(w) for w in find_waste(db, auth.org_id)]
