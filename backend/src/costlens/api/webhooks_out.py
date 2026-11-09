"""Outgoing webhooks: manage endpoints, send a test, see and retry deliveries."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from costlens.accounts.roles import Permission
from costlens.api.deps import SessionDep, plan_feature, require
from costlens.auth.web import AuthContext
from costlens.core import webhooks_out
from costlens.core.errors import AppError, NotFoundError
from costlens.core.netguard import UnsafeURLError, check_public_https_url
from costlens.core.notify import EVENTS
from costlens.db.base import utcnow
from costlens.models import OutgoingWebhook, WebhookDelivery

router = APIRouter(prefix="/api/v1/org/outgoing-webhooks", tags=["outgoing webhooks"])
Admin = Annotated[AuthContext, require(Permission.MANAGE_SETTINGS)]


def _events(value: list[str]) -> list[str]:
    unknown = set(value) - set(EVENTS)
    if unknown:
        raise ValueError(f"unknown events: {', '.join(sorted(unknown))}")
    return sorted(set(value))


class HookIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=10, max_length=2000)
    events: list[str] = Field(default_factory=lambda: list(EVENTS))

    @field_validator("events")
    @classmethod
    def _check(cls, v: list[str]) -> list[str]:
        return _events(v)


class HookPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    enabled: bool | None = None
    events: list[str] | None = None

    @field_validator("events")
    @classmethod
    def _check(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else _events(v)


class HookOut(BaseModel):
    id: uuid.UUID
    name: str
    url_hint: str
    events: list[str]
    enabled: bool
    last_sent_at: datetime | None
    last_status: int | None
    last_error: str | None
    secret: str | None = None  # only in the answer that creates it or rotates it


def _out(h: OutgoingWebhook, secret: str | None = None) -> HookOut:
    host = h.url.split("/")[2] if h.url.count("/") >= 2 else ""
    return HookOut(
        id=h.id,
        name=h.name,
        url_hint=f"https://{host}/…",
        events=h.events or [],
        enabled=h.enabled,
        last_sent_at=h.last_sent_at,
        last_status=h.last_status,
        last_error=h.last_error,
        secret=secret,
    )


def _delivery_out(d: WebhookDelivery) -> dict[str, Any]:
    return {
        "id": str(d.id),
        "event": d.event,
        "status": d.status,
        "attempts": d.attempts,
        "last_status": d.last_status,
        "last_error": d.last_error,
        "created_at": d.created_at.isoformat(),
        "delivered_at": d.delivered_at.isoformat() if d.delivered_at else None,
        "next_attempt_at": d.next_attempt_at.isoformat() if d.next_attempt_at else None,
    }


def _get(db: SessionDep, hook_id: uuid.UUID) -> OutgoingWebhook:
    h = db.get(OutgoingWebhook, hook_id)
    if h is None:
        raise NotFoundError("Webhook not found.")
    return h


@router.get("", response_model=list[HookOut])
def list_hooks(auth: Admin, db: SessionDep) -> list[HookOut]:
    return [_out(h) for h in db.scalars(select(OutgoingWebhook).order_by(OutgoingWebhook.name))]


@router.post(
    "", response_model=HookOut, status_code=201, dependencies=[plan_feature("webhooks_out")]
)
def create_hook(body: HookIn, request: Request, auth: Admin, db: SessionDep) -> HookOut:
    assert auth.org_id is not None  # noqa: S101
    try:
        url = check_public_https_url(body.url, request.app.state.resolver)
    except UnsafeURLError as exc:
        raise AppError(f"This address cannot be used: {exc}.", code="unsafe_url") from exc
    secret = webhooks_out.new_secret()
    h = OutgoingWebhook(
        org_id=auth.org_id, name=body.name, url=url, secret=secret, events=body.events
    )
    db.add(h)
    db.flush()
    return _out(h, secret)


@router.patch("/{hook_id}", response_model=HookOut)
def update_hook(hook_id: uuid.UUID, body: HookPatch, auth: Admin, db: SessionDep) -> HookOut:
    h = _get(db, hook_id)
    for field in ("name", "enabled", "events"):
        value = getattr(body, field)
        if value is not None:
            setattr(h, field, value)
    db.flush()
    return _out(h)


@router.post("/{hook_id}/rotate-secret", response_model=HookOut)
def rotate_secret(hook_id: uuid.UUID, auth: Admin, db: SessionDep) -> HookOut:
    h = _get(db, hook_id)
    h.secret = webhooks_out.new_secret()
    db.flush()
    return _out(h, h.secret)


@router.delete("/{hook_id}", status_code=204)
def delete_hook(hook_id: uuid.UUID, auth: Admin, db: SessionDep) -> Response:
    db.delete(_get(db, hook_id))
    return Response(status_code=204)


@router.post("/{hook_id}/test")
def test_hook(hook_id: uuid.UUID, request: Request, auth: Admin, db: SessionDep) -> dict[str, Any]:
    """Send a ``ping`` now. A failed ping is retried like any other delivery."""
    h = _get(db, hook_id)
    d = webhooks_out.ping(db, h)
    webhooks_out.attempt(db, d, request.app.state.http, request.app.state.resolver)
    return _delivery_out(d)


@router.get("/{hook_id}/deliveries")
def deliveries(hook_id: uuid.UUID, auth: Admin, db: SessionDep) -> list[dict[str, Any]]:
    _get(db, hook_id)
    return [
        _delivery_out(d)
        for d in db.scalars(
            select(WebhookDelivery)
            .where(WebhookDelivery.webhook_id == hook_id)
            .order_by(WebhookDelivery.created_at.desc())
            .limit(50)
        )
    ]


@router.post("/deliveries/{delivery_id}/retry")
def retry(delivery_id: uuid.UUID, request: Request, auth: Admin, db: SessionDep) -> dict[str, Any]:
    d = db.get(WebhookDelivery, delivery_id)
    if d is None:
        raise NotFoundError("Delivery not found.")
    if d.status == "sent":
        raise AppError("This delivery already succeeded.", code="already_sent")
    d.status, d.attempts, d.next_attempt_at = "pending", 0, utcnow()
    webhooks_out.attempt(db, d, request.app.state.http, request.app.state.resolver)
    return _delivery_out(d)
