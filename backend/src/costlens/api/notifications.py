"""Notification channels (Slack, Microsoft Teams)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from costlens.accounts.roles import Permission
from costlens.api.deps import SessionDep, require
from costlens.auth.web import AuthContext
from costlens.core.errors import AppError, NotFoundError
from costlens.core.notify import EVENTS, Message, WebhookError, send, validate_webhook
from costlens.models import NotificationChannel

router = APIRouter(prefix="/api/v1/org/notification-channels", tags=["notifications"])
Admin = Annotated[AuthContext, require(Permission.MANAGE_SETTINGS)]


class ChannelIn(BaseModel):
    kind: Literal["slack", "teams"]
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=10, max_length=2000)
    events: list[str] = Field(default_factory=lambda: list(EVENTS))

    @field_validator("events")
    @classmethod
    def _events(cls, value: list[str]) -> list[str]:
        unknown = set(value) - set(EVENTS)
        if unknown:
            raise ValueError(f"unknown events: {', '.join(sorted(unknown))}")
        return sorted(set(value))


class ChannelPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    enabled: bool | None = None
    events: list[str] | None = None


class ChannelOut(BaseModel):
    id: uuid.UUID
    kind: str
    name: str
    url_hint: str
    events: list[str]
    enabled: bool
    last_sent_at: datetime | None
    last_error: str | None


def _out(c: NotificationChannel) -> ChannelOut:
    return ChannelOut(
        id=c.id,
        kind=c.kind,
        name=c.name,
        url_hint=f"…{c.url[-6:]}",
        events=c.events or [],
        enabled=c.enabled,
        last_sent_at=c.last_sent_at,
        last_error=c.last_error,
    )


def _get(db: SessionDep, channel_id: uuid.UUID) -> NotificationChannel:
    c = db.get(NotificationChannel, channel_id)
    if c is None:
        raise NotFoundError("Channel not found.")
    return c


@router.get("", response_model=list[ChannelOut])
def list_channels(auth: Admin, db: SessionDep) -> list[ChannelOut]:
    return [
        _out(c) for c in db.scalars(select(NotificationChannel).order_by(NotificationChannel.name))
    ]


@router.post("", response_model=ChannelOut, status_code=201)
def create_channel(body: ChannelIn, auth: Admin, db: SessionDep, request: Request) -> ChannelOut:
    try:
        validate_webhook(body.kind, body.url, request.app.state.resolver)
    except WebhookError as exc:
        raise AppError(str(exc), code="bad_webhook") from exc
    c = NotificationChannel(
        org_id=auth.org_id,
        kind=body.kind,
        name=body.name,
        url=body.url,
        events=body.events,
        enabled=True,
    )
    db.add(c)
    db.flush()
    return _out(c)


@router.patch("/{channel_id}", response_model=ChannelOut)
def update_channel(
    channel_id: uuid.UUID, body: ChannelPatch, auth: Admin, db: SessionDep
) -> ChannelOut:
    c = _get(db, channel_id)
    changes = body.model_dump(exclude_unset=True)
    if "events" in changes:
        changes["events"] = ChannelIn._events(changes["events"])  # type: ignore[call-arg]
    for field, value in changes.items():
        setattr(c, field, value)
    db.flush()
    return _out(c)


@router.delete("/{channel_id}", status_code=204)
def delete_channel(channel_id: uuid.UUID, auth: Admin, db: SessionDep) -> Response:
    db.delete(_get(db, channel_id))
    db.flush()
    return Response(status_code=204)


@router.post("/{channel_id}/test", response_model=ChannelOut)
def test_channel(
    channel_id: uuid.UUID, auth: Admin, db: SessionDep, request: Request
) -> ChannelOut:
    c = _get(db, channel_id)
    ok = send(
        c,
        Message(
            title="CostLens test message",
            text="This channel will receive CostLens alerts. No action needed.",
            link=request.app.state.settings.base_url,
            severity="info",
        ),
        request.app.state.http,
    )
    db.commit()  # keep last_error / last_sent_at even when the send failed
    if not ok:
        raise AppError(
            f"The test message was not delivered ({c.last_error}).",
            status=502,
            code="delivery_failed",
        )
    return _out(c)
