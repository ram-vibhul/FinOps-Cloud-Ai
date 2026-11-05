"""Audit log: filter, page and export an organisation's audit trail (admins)."""

from __future__ import annotations

import base64
import csv
import io
import json
import uuid
from collections.abc import Iterator
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import Select, and_, or_, select

from costlens.accounts.roles import Permission
from costlens.api.deps import SessionDep, require
from costlens.auth.web import AuthContext
from costlens.core.errors import AppError
from costlens.models import AuditEvent

router = APIRouter(prefix="/api/v1/org/audit-events", tags=["audit"])
Admin = Annotated[AuthContext, require(Permission.MANAGE_SETTINGS)]
EXPORT_LIMIT = 100_000


class AuditEventOut(BaseModel):
    id: uuid.UUID
    occurred_at: datetime
    actor_type: str
    actor_user_id: uuid.UUID | None
    actor_label: str
    action: str
    target_type: str
    target_id: str | None
    summary: str
    changes: dict[str, Any]
    ip: str | None
    request_id: str | None


class AuditPage(BaseModel):
    items: list[AuditEventOut]
    next_cursor: str | None


def _encode(event: AuditEvent) -> str:
    raw = f"{event.occurred_at.isoformat()}|{event.id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        when, ident = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
        return datetime.fromisoformat(when), uuid.UUID(ident)
    except (ValueError, UnicodeDecodeError) as exc:
        raise AppError("Invalid cursor.", code="invalid_cursor") from exc


def _filtered(
    action: str | None,
    actor: str | None,
    target_type: str | None,
    target_id: str | None,
    since: datetime | None,
    until: datetime | None,
) -> Select[AuditEvent]:
    stmt = select(AuditEvent)
    if action:
        # "membership" matches every membership.* action; "membership.updated" exactly.
        stmt = stmt.where(
            AuditEvent.action == action if "." in action else AuditEvent.action.like(f"{action}.%")
        )
    if actor:
        stmt = stmt.where(AuditEvent.actor_label.ilike(f"%{actor}%"))
    if target_type:
        stmt = stmt.where(AuditEvent.target_type == target_type)
    if target_id:
        stmt = stmt.where(AuditEvent.target_id == target_id)
    if since:
        stmt = stmt.where(AuditEvent.occurred_at >= since)
    if until:
        stmt = stmt.where(AuditEvent.occurred_at < until)
    return stmt.order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())


Filters = dict[str, Any]


@router.get("", response_model=AuditPage)
def list_events(
    auth: Admin,
    db: SessionDep,
    action: str | None = None,
    actor: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AuditPage:
    stmt = _filtered(action, actor, target_type, target_id, since, until)
    if cursor:
        when, ident = _decode(cursor)
        stmt = stmt.where(
            or_(
                AuditEvent.occurred_at < when,
                and_(AuditEvent.occurred_at == when, AuditEvent.id < ident),
            )
        )
    rows = db.scalars(stmt.limit(limit + 1)).all()
    page = rows[:limit]
    return AuditPage(
        items=[AuditEventOut.model_validate(r, from_attributes=True) for r in page],
        next_cursor=_encode(page[-1]) if len(rows) > limit else None,
    )


CSV_COLUMNS = [
    "occurred_at",
    "actor_type",
    "actor_label",
    "action",
    "target_type",
    "target_id",
    "summary",
    "changes",
    "ip",
    "request_id",
]


@router.get("/export", response_class=StreamingResponse)
def export_events(
    auth: Admin,
    db: SessionDep,
    action: str | None = None,
    actor: str | None = None,
    target_type: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> StreamingResponse:
    """CSV export (newest first, up to 100,000 rows) for auditors and spreadsheets."""
    rows = db.scalars(
        _filtered(action, actor, target_type, None, since, until).limit(EXPORT_LIMIT)
    ).all()

    def generate() -> Iterator[str]:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(CSV_COLUMNS)
        for r in rows:
            writer.writerow(
                [
                    r.occurred_at.isoformat(),
                    r.actor_type,
                    _safe(r.actor_label),
                    r.action,
                    r.target_type,
                    r.target_id or "",
                    _safe(r.summary),
                    json.dumps(r.changes, sort_keys=True),
                    r.ip or "",
                    r.request_id or "",
                ]
            )
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate(0)

    return StreamingResponse(
        generate(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="costlens-audit-log.csv"'},
    )


def _safe(value: str) -> str:
    """Neutralise spreadsheet formula injection (=, +, -, @ at the start)."""
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value
