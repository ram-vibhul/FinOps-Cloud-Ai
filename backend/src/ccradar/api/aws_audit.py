"""AWS audit: run the service-by-service checklist, history and the report."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select

from ccradar.accounts.roles import Permission
from ccradar.api.deps import SessionDep, require
from ccradar.auth.web import AuthContext
from ccradar.aws_audit.catalogue import AREAS, CHECKS
from ccradar.aws_audit.service import run_audit
from ccradar.core.errors import NotFoundError
from ccradar.models import AuditResult, AuditRun, User

router = APIRouter(prefix="/api/v1/audits", tags=["audit"])
Reader = Annotated[AuthContext, require(Permission.READ)]
Writer = Annotated[AuthContext, require(Permission.WRITE)]
ORDER = {"saving": 0, "more_data": 1, "manual": 2, "fine": 3}


class RunOut(BaseModel):
    id: uuid.UUID
    started_at: datetime
    finished_at: datetime | None
    status: str
    trigger: str
    requested_by: str | None = None
    currency: str
    summary: dict[str, Any]


class ResultOut(BaseModel):
    check_id: str
    area: str
    service: str
    title: str
    status: str
    reason: str
    spend_monthly: float
    savings_monthly: float
    finding_count: int
    details: dict[str, Any]


def _run_out(run: AuditRun, who: str | None = None) -> RunOut:
    out = RunOut.model_validate(run, from_attributes=True)
    out.requested_by = who
    return out


@router.get("/catalogue")
def catalogue(auth: Reader) -> dict[str, Any]:
    return {
        "areas": AREAS,
        "checks": [
            {
                "id": c.id,
                "area": c.area,
                "service": c.service,
                "title": c.title,
                "automatic": bool(c.detectors),
            }
            for c in CHECKS
        ],
    }


@router.get("", response_model=list[RunOut])
def list_runs(
    auth: Reader, db: SessionDep, limit: Annotated[int, Query(ge=1, le=100)] = 20
) -> list[RunOut]:
    rows = db.execute(
        select(AuditRun, User.name, User.email)
        .outerjoin(User, User.id == AuditRun.requested_by_id)
        .order_by(AuditRun.started_at.desc())
        .limit(limit)
    ).all()
    return [_run_out(r, (name or email) if r.requested_by_id else None) for r, name, email in rows]


@router.post("", response_model=RunOut, status_code=201)
def start_run(auth: Writer, db: SessionDep) -> RunOut:
    """Run the detectors and the whole checklist now (it also runs every Monday)."""
    assert auth.org_id is not None  # noqa: S101
    run = run_audit(db, auth.org_id, user_id=auth.user.id)
    return _run_out(run, auth.user.name or auth.user.email)


@router.get("/{run_id}")
def get_run(run_id: uuid.UUID, auth: Reader, db: SessionDep) -> dict[str, Any]:
    run = db.get(AuditRun, run_id)
    if run is None:
        raise NotFoundError("Audit not found.")
    who = db.get(User, run.requested_by_id) if run.requested_by_id else None
    results = db.scalars(select(AuditResult).where(AuditResult.run_id == run.id)).all()
    items = sorted(
        (ResultOut.model_validate(r, from_attributes=True) for r in results),
        key=lambda r: (list(AREAS).index(r.area), ORDER[r.status], -r.savings_monthly, r.title),
    )
    return {"run": _run_out(run, (who.name or who.email) if who else None), "results": items}
