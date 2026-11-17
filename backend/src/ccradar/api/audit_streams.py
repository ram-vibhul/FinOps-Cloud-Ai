"""Audit-log streaming to a SIEM (11.42): add destinations, send a test, see status."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from ccradar.accounts.roles import Permission
from ccradar.api.deps import SessionDep, plan_feature, require
from ccradar.audit import streams
from ccradar.auth.web import AuthContext
from ccradar.core.errors import AppError, NotFoundError
from ccradar.db.base import utcnow
from ccradar.models import AuditStream, Organization

router = APIRouter(prefix="/api/v1/org/audit-streams", tags=["audit-log streaming"])
Admin = Annotated[AuthContext, require(Permission.MANAGE_SETTINGS)]
Kind = Literal["splunk_hec", "sentinel", "datadog", "syslog", "webhook"]


class StreamIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: Kind
    settings: dict[str, Any] = Field(default_factory=dict)
    secret: str | None = Field(default=None, max_length=4000)
    # Where to start: only events from now on, or everything still kept (back-fill).
    start: Literal["now", "all"] = "now"


class StreamPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    enabled: bool | None = None
    settings: dict[str, Any] | None = None
    secret: str | None = Field(default=None, max_length=4000)


class StreamOut(BaseModel):
    id: uuid.UUID
    name: str
    kind: str
    kind_label: str
    settings: dict[str, Any]
    has_secret: bool
    enabled: bool
    sent_total: int
    position: datetime | None
    failures: int
    next_attempt_at: datetime | None
    last_sent_at: datetime | None
    last_status: int | None
    last_error: str | None
    signing_secret: str | None = None  # only when created or rotated


def _out(s: AuditStream, signing_secret: str | None = None) -> StreamOut:
    return StreamOut(
        id=s.id,
        name=s.name,
        kind=s.kind,
        kind_label=streams.KINDS.get(s.kind, s.kind),
        settings=s.settings or {},
        has_secret=bool(s.secret),
        enabled=s.enabled,
        sent_total=s.sent_total,
        position=s.cursor_at,
        failures=s.failures,
        next_attempt_at=s.next_attempt_at,
        last_sent_at=s.last_sent_at,
        last_status=s.last_status,
        last_error=s.last_error,
        signing_secret=signing_secret,
    )


def _get(db: SessionDep, stream_id: uuid.UUID) -> AuditStream:
    s = db.get(AuditStream, stream_id)
    if s is None:
        raise NotFoundError("Stream not found.")
    return s


def _settings(kind: str, raw: dict[str, Any], request: Request) -> dict[str, Any]:
    try:
        return streams.clean_settings(kind, raw, request.app.state.resolver)
    except streams.StreamError as exc:
        raise AppError(str(exc), code="invalid_stream") from exc


def _transport(request: Request) -> streams.Transport:
    return streams.Transport(
        http=request.app.state.http,
        resolver=request.app.state.resolver,
        syslog=getattr(request.app.state, "syslog_sender", None),
    )


@router.get("", response_model=list[StreamOut])
def list_streams(auth: Admin, db: SessionDep) -> list[StreamOut]:
    return [_out(s) for s in db.scalars(select(AuditStream).order_by(AuditStream.name))]


@router.get("/kinds")
def kinds(auth: Admin) -> dict[str, Any]:
    return {"kinds": streams.KINDS, "datadog_sites": list(streams.DATADOG_SITES)}


@router.post(
    "",
    response_model=StreamOut,
    status_code=201,
    dependencies=[plan_feature("audit_streaming")],
)
def create_stream(body: StreamIn, request: Request, auth: Admin, db: SessionDep) -> StreamOut:
    assert auth.org_id is not None  # noqa: S101
    settings = _settings(body.kind, body.settings, request)
    if streams.needs_secret(body.kind) and not (body.secret or "").strip():
        raise AppError("This destination needs its token or key.", code="secret_required")
    signing = streams.new_signing_secret()
    s = AuditStream(
        org_id=auth.org_id,
        name=body.name.strip(),
        kind=body.kind,
        settings=settings,
        secret=(body.secret or "").strip() or None,
        signing_secret=signing,
        # A cursor just before now: only new events. None: everything still kept.
        cursor_at=None if body.start == "all" else utcnow() - timedelta(seconds=1),
        cursor_id=None,
    )
    db.add(s)
    db.flush()
    return _out(s, signing)


@router.patch("/{stream_id}", response_model=StreamOut)
def update_stream(
    stream_id: uuid.UUID, body: StreamPatch, request: Request, auth: Admin, db: SessionDep
) -> StreamOut:
    s = _get(db, stream_id)
    if body.name is not None:
        s.name = body.name.strip()
    if body.enabled is not None:
        s.enabled = body.enabled
        if body.enabled:
            s.next_attempt_at = None  # try again now
    if body.settings is not None:
        s.settings = _settings(s.kind, body.settings, request)
    if body.secret is not None and body.secret.strip():
        s.secret = body.secret.strip()
        s.next_attempt_at = None
    db.flush()
    return _out(s)


@router.post("/{stream_id}/rotate-signing-secret", response_model=StreamOut)
def rotate(stream_id: uuid.UUID, auth: Admin, db: SessionDep) -> StreamOut:
    s = _get(db, stream_id)
    s.signing_secret = streams.new_signing_secret()
    db.flush()
    return _out(s, s.signing_secret)


@router.delete("/{stream_id}", status_code=204)
def delete_stream(stream_id: uuid.UUID, auth: Admin, db: SessionDep) -> Response:
    db.delete(_get(db, stream_id))
    return Response(status_code=204)


@router.post("/{stream_id}/test")
def test_stream(
    stream_id: uuid.UUID, request: Request, auth: Admin, db: SessionDep
) -> dict[str, Any]:
    """Send one test event now (does not move the stream's position)."""
    s = _get(db, stream_id)
    org = db.get(Organization, s.org_id)
    try:
        streams.send_test(s, _transport(request), org.slug if org else None)
    except streams.StreamError as exc:
        s.last_status, s.last_error = exc.status, str(exc)[:500]
        db.flush()
        return {"ok": False, "error": str(exc)}
    db.flush()
    return {"ok": True, "error": None}


@router.post("/{stream_id}/send-now", response_model=StreamOut)
def send_now(stream_id: uuid.UUID, request: Request, auth: Admin, db: SessionDep) -> StreamOut:
    """Send what is waiting now instead of at the next minute (also retries a failing one)."""
    s = _get(db, stream_id)
    s.next_attempt_at = None
    streams.run_stream(db, s, _transport(request))
    return _out(s)
