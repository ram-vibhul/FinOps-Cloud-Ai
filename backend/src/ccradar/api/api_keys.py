"""Organisation API keys (admins). The key is shown once, when it is created."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from ccradar.accounts import api_keys
from ccradar.accounts.roles import Permission
from ccradar.api.deps import SessionDep, plan_feature, require
from ccradar.auth.web import AuthContext
from ccradar.core.errors import AppError, NotFoundError
from ccradar.db.base import utcnow
from ccradar.models import OrgApiKey, User

router = APIRouter(prefix="/api/v1/org/api-keys", tags=["api keys"])
Admin = Annotated[AuthContext, require(Permission.MANAGE_SETTINGS)]


class KeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scope: Literal["read", "write", "admin"] = "read"
    expires_at: datetime | None = None
    can_investigate: bool = False  # MCP may start Kubernetes investigations (8.14)


class KeyOut(BaseModel):
    id: uuid.UUID
    name: str
    scope: str
    key_hint: str
    created_by: str | None
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    can_investigate: bool = False
    key: str | None = None  # only in the answer that creates it


def _out(db: SessionDep, k: OrgApiKey, secret: str | None = None) -> KeyOut:
    user = db.get(User, k.created_by_id)
    return KeyOut(
        id=k.id,
        name=k.name,
        scope=k.scope,
        key_hint=k.key_hint,
        created_by=user.email if user else None,
        created_at=k.created_at,
        expires_at=k.expires_at,
        last_used_at=k.last_used_at,
        can_investigate=k.can_investigate,
        key=secret,
    )


@router.get("", response_model=list[KeyOut])
def list_keys(auth: Admin, db: SessionDep) -> list[KeyOut]:
    rows = db.scalars(
        select(OrgApiKey).where(OrgApiKey.revoked_at.is_(None)).order_by(OrgApiKey.created_at)
    )
    return [_out(db, k) for k in rows]


@router.post("", response_model=KeyOut, status_code=201, dependencies=[plan_feature("api_keys")])
def create_key(body: KeyIn, auth: Admin, db: SessionDep) -> KeyOut:
    assert auth.org_id is not None  # noqa: S101
    if body.expires_at is not None and body.expires_at <= utcnow():
        raise AppError("The expiry date must be in the future.", code="bad_expiry")
    key, secret = api_keys.issue(db, auth.org_id, auth.user.id, body.name.strip(), body.scope)
    key.expires_at = body.expires_at
    key.can_investigate = body.can_investigate
    db.flush()
    return _out(db, key, secret)


@router.delete("/{key_id}", status_code=204)
def revoke_key(key_id: uuid.UUID, auth: Admin, db: SessionDep) -> Response:
    key = db.get(OrgApiKey, key_id)
    if key is None or key.revoked_at is not None:
        raise NotFoundError("Key not found.")
    key.revoked_at = utcnow()
    db.flush()
    return Response(status_code=204)
