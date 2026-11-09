"""Report schedules (emailed PDF/CSV) and on-demand downloads."""

from __future__ import annotations

import re
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from costlens.accounts.roles import Permission
from costlens.api.deps import SessionDep, require
from costlens.auth.web import AuthContext
from costlens.core.errors import NotFoundError
from costlens.models import ReportSchedule
from costlens.reports import scheduled

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])
Reader = Annotated[AuthContext, require(Permission.READ)]
Writer = Annotated[AuthContext, require(Permission.WRITE)]
Kind = Literal["cost_summary", "savings", "budgets", "showback", "leadership"]


class ScheduleIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: Kind
    frequency: Literal["daily", "weekly", "monthly"] = "monthly"
    weekday: int = Field(default=0, ge=0, le=6)
    day: int = Field(default=1, ge=1, le=28)
    format: Literal["pdf", "csv"] = "pdf"
    recipients: list[str] = Field(min_length=1, max_length=20)
    params: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True

    @field_validator("recipients")
    @classmethod
    def _emails(cls, value: list[str]) -> list[str]:
        for e in value:
            if not re.fullmatch(r"[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,63}", e):
                raise ValueError(f"not an email address: {e}")
        return [e.lower() for e in value]


def _org(auth: AuthContext) -> uuid.UUID:
    assert auth.org_id is not None  # noqa: S101
    return auth.org_id


def _out(s: ReportSchedule) -> dict[str, Any]:
    return {
        "id": str(s.id),
        "name": s.name,
        "kind": s.kind,
        "kind_label": scheduled.KINDS.get(s.kind, s.kind),
        "frequency": s.frequency,
        "weekday": s.weekday,
        "day": s.day,
        "format": s.format,
        "recipients": s.recipients,
        "params": s.params,
        "enabled": s.enabled,
        "last_sent_at": s.last_sent_at.isoformat() if s.last_sent_at else None,
        "last_error": s.last_error,
    }


@router.get("/schedules")
def list_schedules(auth: Reader, db: SessionDep) -> list[dict[str, Any]]:
    return [_out(s) for s in db.scalars(select(ReportSchedule).order_by(ReportSchedule.name))]


@router.post("/schedules", status_code=201)
def create_schedule(body: ScheduleIn, auth: Writer, db: SessionDep) -> dict[str, Any]:
    s = ReportSchedule(org_id=auth.org_id, created_by_id=auth.user.id, **body.model_dump())
    db.add(s)
    db.flush()
    return _out(s)


@router.put("/schedules/{schedule_id}")
def update_schedule(
    schedule_id: uuid.UUID, body: ScheduleIn, auth: Writer, db: SessionDep
) -> dict[str, Any]:
    s = db.get(ReportSchedule, schedule_id)
    if s is None:
        raise NotFoundError("Schedule not found.")
    for field, value in body.model_dump().items():
        setattr(s, field, value)
    db.flush()
    return _out(s)


@router.delete("/schedules/{schedule_id}", status_code=204)
def delete_schedule(schedule_id: uuid.UUID, auth: Writer, db: SessionDep) -> Response:
    s = db.get(ReportSchedule, schedule_id)
    if s is None:
        raise NotFoundError("Schedule not found.")
    db.delete(s)
    db.flush()
    return Response(status_code=204)


@router.post("/schedules/{schedule_id}/send")
def send_now(
    schedule_id: uuid.UUID, auth: Writer, db: SessionDep, request: Request
) -> dict[str, int]:
    s = db.get(ReportSchedule, schedule_id)
    if s is None:
        raise NotFoundError("Schedule not found.")
    jobs = request.app.state.jobs
    return {
        "sent": scheduled.send(
            db, _org(auth), s, lambda message: jobs.enqueue("email.send", message=message)
        )
    }


@router.get("/leadership")
def leadership_preview(
    auth: Reader,
    db: SessionDep,
    month: Annotated[str | None, Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")] = None,
) -> dict[str, Any]:
    """The one-page leadership report as data (last full month unless ``month`` is given)."""
    from datetime import date

    from costlens.reports.leadership import build

    return build(db, _org(auth), date.fromisoformat(f"{month}-01") if month else None)


@router.get("/download")
def download(
    auth: Reader,
    db: SessionDep,
    kind: Kind,
    format: Annotated[Literal["pdf", "csv"], Query()] = "pdf",
    frequency: Literal["daily", "weekly", "monthly"] = "monthly",
    dimension: Annotated[str, Query(max_length=140)] = "tag:team",
) -> Response:
    filename, content_type, data, _ = scheduled.render(
        db, _org(auth), kind, frequency, {"dimension": dimension}, format
    )
    return Response(
        data,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
