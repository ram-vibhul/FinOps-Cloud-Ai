"""On-demand read-only agent tasks (fetch logs, describe) requested from the UI."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel

from costlens.accounts.roles import Permission
from costlens.api.deps import SessionDep, require
from costlens.auth.web import AuthContext
from costlens.core.errors import NotFoundError
from costlens.k8s import tasks
from costlens.models import K8sAgentTask, K8sCluster

router = APIRouter(prefix="/api/v1/k8s", tags=["kubernetes"])
Reader = Annotated[AuthContext, require(Permission.READ)]
Writer = Annotated[AuthContext, require(Permission.WRITE)]  # logs can be sensitive


class TaskIn(BaseModel):
    kind: Literal["logs", "describe", "promql"]
    params: dict[str, Any]


class TaskOut(BaseModel):
    id: uuid.UUID
    kind: str
    params: dict[str, Any]
    status: str
    result: str | None
    error: str | None
    created_at: datetime
    finished_at: datetime | None


@router.post("/clusters/{cluster_id}/tasks", response_model=TaskOut, status_code=202)
def create_task(cluster_id: uuid.UUID, body: TaskIn, auth: Writer, db: SessionDep) -> TaskOut:
    cluster = db.get(K8sCluster, cluster_id)
    if cluster is None:
        raise NotFoundError("Cluster not found.")
    task = tasks.create(db, cluster, body.kind, body.params, auth.user.id)
    return TaskOut.model_validate(task, from_attributes=True)


@router.get("/tasks/{task_id}", response_model=TaskOut)
def get_task(task_id: uuid.UUID, auth: Reader, db: SessionDep) -> TaskOut:
    task = db.get(K8sAgentTask, task_id)
    if task is None:
        raise NotFoundError("Task not found.")
    tasks.expire_old(db, task.cluster_id)
    db.refresh(task)
    return TaskOut.model_validate(task, from_attributes=True)
