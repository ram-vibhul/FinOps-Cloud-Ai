"""Kubernetes workloads (with quick health) and incident investigations."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from ccradar.accounts.roles import Permission
from ccradar.ai.writer import TextWriter
from ccradar.api.deps import SessionDep, require
from ccradar.auth.web import AuthContext
from ccradar.core.errors import AppError, NotFoundError
from ccradar.costs.anomalies import alert_settings
from ccradar.db.base import utcnow
from ccradar.k8s.correlate import attach
from ccradar.k8s.fixcheck import check
from ccradar.k8s.health import workload_health
from ccradar.k8s.investigate import gather, investigate, reword
from ccradar.models import ChangeEvent, K8sCluster, K8sInvestigation, Organization

router = APIRouter(prefix="/api/v1/k8s", tags=["kubernetes"])
Reader = Annotated[AuthContext, require(Permission.READ)]
Writer = Annotated[AuthContext, require(Permission.WRITE)]


class InvestigationIn(BaseModel):
    cluster_id: uuid.UUID
    namespace: str | None = Field(default=None, max_length=253)
    kind: str | None = Field(default=None, max_length=40)
    name: str | None = Field(default=None, max_length=253)
    hours: int = Field(default=6, ge=1, le=72)
    # A custom window instead of "the last N hours" (e.g. last night's incident).
    start: datetime | None = None
    end: datetime | None = None

    @model_validator(mode="after")
    def _window(self) -> InvestigationIn:
        if (self.start is None) != (self.end is None):
            raise ValueError("Give both start and end, or neither.")
        if self.start and self.end:
            if self.end <= self.start:
                raise ValueError("The end must be after the start.")
            if self.end - self.start > timedelta(hours=72):
                raise ValueError("A window can be at most 72 hours.")
        return self


def _cluster(db: SessionDep, cluster_id: uuid.UUID) -> K8sCluster:
    cluster = db.get(K8sCluster, cluster_id)
    if cluster is None:
        raise NotFoundError("Cluster not found.")
    return cluster


def _run(
    db: SessionDep, body: InvestigationIn, writer: TextWriter | None, settings: Any = None
) -> tuple[K8sCluster, dict[str, Any], Any, Any]:
    cluster = _cluster(db, body.cluster_id)
    end = utcnow()
    if cluster.last_seen_at is not None:  # the latest state we actually know
        seen = (
            cluster.last_seen_at
            if cluster.last_seen_at.tzinfo
            else cluster.last_seen_at.replace(tzinfo=UTC)
        )
        end = min(end, seen + timedelta(minutes=1))
    start = end - timedelta(hours=body.hours)
    if body.start and body.end:
        custom_end = body.end if body.end.tzinfo else body.end.replace(tzinfo=UTC)
        custom_start = body.start if body.start.tzinfo else body.start.replace(tzinfo=UTC)
        if custom_start >= end:
            raise AppError("That window is in the future.", code="future_window")
        start, end = custom_start, min(custom_end, end)
    ev = gather(
        db, cluster, namespace=body.namespace, kind=body.kind, name=body.name, start=start, end=end
    )
    result = investigate(ev, cluster.name)
    attach(db, cluster, result, body.namespace, body.kind, body.name, start, end)
    org = db.get(Organization, cluster.org_id)
    if writer is not None and org is not None and alert_settings(org).ai_explanations:
        from ccradar.billing.metering import use_ai

        if settings is None or use_ai(db, settings):  # B.12: counted against the plan
            reword(writer, result)
    return cluster, result, start, end


@router.get("/workloads")
def workloads(
    auth: Reader, db: SessionDep, cluster_id: Annotated[uuid.UUID, Query()]
) -> list[dict[str, Any]]:
    """Workloads with pod counts and a quick health status (failing / warning / ok)."""
    _cluster(db, cluster_id)
    return workload_health(db, cluster_id)


@router.post("/investigations/run")
def run_investigation(
    body: InvestigationIn, auth: Reader, db: SessionDep, request: Request
) -> dict[str, Any]:
    """Investigate now without saving (read-only: also works in the demo)."""
    _, result, start, end = _run(db, body, request.app.state.ai, request.app.state.settings)
    return {**result, "window_start": start.isoformat(), "window_end": end.isoformat()}


def _out(i: K8sInvestigation, names: dict[uuid.UUID, str]) -> dict[str, Any]:
    return {
        "id": str(i.id),
        "cluster_id": str(i.cluster_id),
        "cluster": names.get(i.cluster_id),
        "namespace": i.namespace,
        "kind": i.kind,
        "name": i.name,
        "title": i.title,
        "status": i.status,
        "trigger": i.trigger,
        "window_start": i.window_start.isoformat(),
        "window_end": i.window_end.isoformat(),
        "created_at": i.created_at.isoformat(),
        "result": i.result,
        "real_cause": i.real_cause,
        "cause_note": i.cause_note,
        "fix_note": i.fix_note,
        "top_cause": i.top_cause,
        "cause_rank": i.cause_rank,
        "closed_at": i.closed_at.isoformat() if i.closed_at else None,
    }


@router.post("/investigations", status_code=201)
def create_investigation(
    body: InvestigationIn, auth: Writer, db: SessionDep, request: Request
) -> dict[str, Any]:
    cluster, result, start, end = _run(db, body, request.app.state.ai, request.app.state.settings)
    inv = K8sInvestigation(
        org_id=auth.org_id,
        cluster_id=cluster.id,
        namespace=body.namespace,
        kind=body.kind,
        name=body.name,
        window_start=start,
        window_end=end,
        trigger="manual",
        title=f"{result['target']}: {result['summary']}"[:300],
        result=result,
        created_by_id=auth.user.id,
    )
    db.add(inv)
    db.flush()
    return _out(inv, {cluster.id: cluster.name})


@router.get("/investigations")
def list_investigations(auth: Reader, db: SessionDep) -> list[dict[str, Any]]:
    names = dict(db.execute(select(K8sCluster.id, K8sCluster.name)).all())
    rows = db.scalars(
        select(K8sInvestigation).order_by(K8sInvestigation.created_at.desc()).limit(100)
    )
    return [{k: v for k, v in _out(i, names).items() if k != "result"} for i in rows]


@router.get("/investigations/{investigation_id}")
def get_investigation(investigation_id: uuid.UUID, auth: Reader, db: SessionDep) -> dict[str, Any]:
    inv = db.get(K8sInvestigation, investigation_id)
    if inv is None:
        raise NotFoundError("Investigation not found.")
    return _out(inv, dict(db.execute(select(K8sCluster.id, K8sCluster.name)).all()))


@router.post("/investigations/{investigation_id}/refresh")
def refresh_investigation(
    investigation_id: uuid.UUID, auth: Writer, db: SessionDep, request: Request
) -> dict[str, Any]:
    """Re-run with fresh evidence for the same target and window length."""
    inv = db.get(K8sInvestigation, investigation_id)
    if inv is None:
        raise NotFoundError("Investigation not found.")
    hours = max(1, round((inv.window_end - inv.window_start).total_seconds() / 3600))
    cluster, result, start, end = _run(
        db,
        InvestigationIn(
            cluster_id=inv.cluster_id,
            namespace=inv.namespace,
            kind=inv.kind,
            name=inv.name,
            hours=min(hours, 72),
        ),
        request.app.state.ai,
        request.app.state.settings,
    )
    inv.result, inv.window_start, inv.window_end = result, start, end
    inv.title = f"{result['target']}: {result['summary']}"[:300]
    db.flush()
    return _out(inv, {cluster.id: cluster.name})


class FixCheckIn(BaseModel):
    cluster_id: uuid.UUID
    namespace: str = Field(max_length=253)
    kind: str = Field(max_length=40)
    name: str = Field(max_length=253)
    fix_at: datetime | None = None
    change_id: uuid.UUID | None = None  # use a recorded change (deploy, rollback) as the fix
    incident_start: datetime | None = None


def _fix_time(db: SessionDep, fix_at: datetime | None, change_id: uuid.UUID | None) -> datetime:
    if change_id is not None:
        change = db.get(ChangeEvent, change_id)
        if change is None:
            raise NotFoundError("Change not found.")
        return change.occurred_at
    if fix_at is None:
        raise AppError(
            "Say when the fix was made (a time or a recorded change).", code="no_fix_time"
        )
    return fix_at if fix_at.tzinfo else fix_at.replace(tzinfo=UTC)


@router.post("/fix-check")
def fix_check(body: FixCheckIn, auth: Reader, db: SessionDep) -> dict[str, Any]:
    """Before / during / after comparison with a verdict (read-only)."""
    cluster = _cluster(db, body.cluster_id)
    return check(
        db,
        cluster,
        namespace=body.namespace,
        kind=body.kind,
        name=body.name,
        fix_at=_fix_time(db, body.fix_at, body.change_id),
        now=utcnow(),
        incident_start=body.incident_start,
    )


class SavedFixCheckIn(BaseModel):
    fix_at: datetime | None = None
    change_id: uuid.UUID | None = None


@router.post("/investigations/{investigation_id}/fix-check")
def save_fix_check(
    investigation_id: uuid.UUID, body: SavedFixCheckIn, auth: Writer, db: SessionDep
) -> dict[str, Any]:
    inv = db.get(K8sInvestigation, investigation_id)
    if inv is None:
        raise NotFoundError("Investigation not found.")
    if not (inv.namespace and inv.kind and inv.name):
        raise AppError(
            "A fix check needs an investigation of a single workload.", code="not_a_workload"
        )
    cluster = _cluster(db, inv.cluster_id)
    result = check(
        db,
        cluster,
        namespace=inv.namespace,
        kind=inv.kind,
        name=inv.name,
        fix_at=_fix_time(db, body.fix_at, body.change_id),
        now=utcnow(),
    )
    inv.result = {**(inv.result or {}), "fix_check": result}
    db.flush()
    return result


class CloseIn(BaseModel):
    real_cause: str = Field(max_length=40)
    cause_note: str | None = Field(default=None, max_length=1000)
    fix_note: str | None = Field(default=None, max_length=1000)


@router.post("/investigations/{investigation_id}/close")
def close_investigation(
    investigation_id: uuid.UUID, body: CloseIn, auth: Writer, db: SessionDep
) -> dict[str, Any]:
    """Record what really caused the problem and what fixed it."""
    from ccradar.k8s.investigate import CHECK_LABELS

    if body.real_cause not in CHECK_LABELS and body.real_cause != "other":
        raise AppError(
            "Choose one of the listed causes, or Other.", status=422, code="unknown_cause"
        )
    inv = db.get(K8sInvestigation, investigation_id)
    if inv is None:
        raise NotFoundError("Investigation not found.")
    ranked = [c["check"] for c in (inv.result or {}).get("causes", [])]
    inv.real_cause, inv.cause_note, inv.fix_note = body.real_cause, body.cause_note, body.fix_note
    inv.top_cause = ranked[0] if ranked else None
    inv.cause_rank = ranked.index(body.real_cause) + 1 if body.real_cause in ranked else None
    inv.status, inv.closed_at, inv.closed_by_id = "closed", utcnow(), auth.user.id
    db.flush()
    return _out(inv, {inv.cluster_id: _cluster(db, inv.cluster_id).name})


@router.get("/accuracy")
def diagnosis_accuracy(auth: Reader, db: SessionDep) -> dict[str, Any]:
    """Top-ranked-cause accuracy from investigations closed with the real cause."""
    from ccradar.k8s.accuracy import accuracy

    return accuracy(db)
