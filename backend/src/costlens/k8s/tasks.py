"""Read-only agent tasks: an allow-list of kinds with strictly validated parameters.
The agent re-checks the same rules before doing anything."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from costlens.core.errors import AppError
from costlens.core.redact import redact
from costlens.db.base import utcnow
from costlens.k8s.service import agent_config
from costlens.models import K8sAgentTask, K8sCluster

NAME = r"^[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?$"
KINDS = ("logs", "describe", "promql")
MAX_QUEUED = 10
EXPIRE_AFTER = timedelta(minutes=10)
MAX_RESULT = 256 * 1024


class LogsParams(BaseModel):
    namespace: str = Field(pattern=NAME)
    pod: str = Field(pattern=NAME)
    container: str | None = Field(default=None, pattern=NAME)
    previous: bool = False  # the container that crashed before the current one
    tail_lines: int = Field(default=200, ge=1, le=500)
    since_seconds: int | None = Field(default=None, ge=1, le=86_400)


class PromqlParams(BaseModel):
    promql: str = Field(min_length=1, max_length=500)

    @field_validator("promql")
    @classmethod
    def _valid(cls, value: str) -> str:
        from costlens.k8s.promql import problem

        reason = problem(value)
        if reason:
            raise ValueError(f"This query cannot be used: {reason}.")
        return value.strip()


class DescribeParams(BaseModel):
    namespace: str = Field(pattern=NAME)
    kind: Literal["Pod", "Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob"]
    name: str = Field(pattern=NAME)


def validate(cluster: K8sCluster, kind: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        if kind == "logs":
            clean = LogsParams.model_validate(params).model_dump(exclude_none=True)
        elif kind == "describe":
            clean = DescribeParams.model_validate(params).model_dump()
        elif kind == "promql":  # test a customer query once (read-only)
            clean = PromqlParams.model_validate(params).model_dump()
        else:
            clean = None
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        raise AppError(
            f"Invalid task parameters ({problems}).", status=422, code="invalid_task"
        ) from exc
    if clean is None:
        raise AppError(
            "Only fetching logs and describing objects are allowed.", code="task_not_allowed"
        )
    if kind == "promql":
        return clean
    cfg = agent_config(cluster)
    ns = clean["namespace"]
    include, exclude = cfg["include_namespaces"], cfg["exclude_namespaces"]
    if (include and ns not in include) or (not include and ns in exclude):
        raise AppError(
            f"Namespace {ns} is not watched for this cluster.", code="namespace_not_watched"
        )
    return clean


def create(
    db: Session, cluster: K8sCluster, kind: str, params: dict[str, Any], user_id: uuid.UUID | None
) -> K8sAgentTask:
    clean = validate(cluster, kind, params)
    queued = db.scalar(
        select(func.count())
        .select_from(K8sAgentTask)
        .where(
            K8sAgentTask.cluster_id == cluster.id, K8sAgentTask.status.in_(("queued", "running"))
        )
    )
    if (queued or 0) >= MAX_QUEUED:
        raise AppError(
            "Too many tasks waiting for this cluster; try again shortly.",
            status=429,
            code="too_many_tasks",
        )
    task = K8sAgentTask(
        org_id=cluster.org_id,
        cluster_id=cluster.id,
        kind=kind,
        params=clean,
        status="queued",
        requested_by_id=user_id,
    )
    db.add(task)
    db.flush()
    return task


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def expire_old(db: Session, cluster_id: uuid.UUID) -> None:
    now = utcnow()
    db.execute(
        update(K8sAgentTask)
        .where(
            K8sAgentTask.cluster_id == cluster_id,
            K8sAgentTask.status.in_(("queued", "running")),
            K8sAgentTask.created_at < now - EXPIRE_AFTER,
        )
        .values(
            status="expired",
            error="The agent did not pick this up in time (is it running?).",
            finished_at=now,
        )
        .execution_options(synchronize_session=False)  # callers refresh what they show
    )


def claim(db: Session, cluster: K8sCluster, limit: int = 5) -> list[dict[str, Any]]:
    expire_old(db, cluster.id)
    tasks = db.scalars(
        select(K8sAgentTask)
        .where(
            K8sAgentTask.cluster_id == cluster.id,
            K8sAgentTask.status == "queued",
        )
        .order_by(K8sAgentTask.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    now = utcnow()
    for t in tasks:
        t.status, t.claimed_at = "running", now
    db.flush()
    return [{"id": str(t.id), "kind": t.kind, "params": t.params} for t in tasks]


def complete(
    db: Session,
    cluster: K8sCluster,
    task_id: uuid.UUID,
    *,
    ok: bool,
    text: str | None,
    error: str | None,
) -> K8sAgentTask:
    task = db.get(K8sAgentTask, task_id)
    if task is None or task.cluster_id != cluster.id or task.status != "running":
        raise AppError("Unknown or finished task.", status=404, code="unknown_task")
    task.status = "done" if ok else "failed"
    body = redact(text or "")
    if len(body) > MAX_RESULT:
        body = body[-MAX_RESULT:]  # keep the end: the latest lines matter most
    task.result = body if ok else None
    task.error = None if ok else redact(error or "failed")[:1000]
    task.finished_at = utcnow()
    db.flush()
    return task
