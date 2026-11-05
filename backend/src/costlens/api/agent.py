"""Endpoints the in-cluster agent calls, authenticated by its cluster token."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field, field_validator

from costlens.api.deps import SessionDep
from costlens.core.errors import AppError
from costlens.db.base import utcnow
from costlens.k8s import service
from costlens.models import K8sCluster

router = APIRouter(prefix="/api/v1/agent", tags=["agent"], include_in_schema=False)
MAX_BODY = 20 * 1024 * 1024


def agent_cluster(
    request: Request,
    db: SessionDep,
    authorization: Annotated[str | None, Header()] = None,
) -> K8sCluster:
    if int(request.headers.get("content-length") or 0) > MAX_BODY:
        raise AppError("Payload too large.", status=413, code="too_large")
    token = authorization[7:] if authorization and authorization.startswith("Bearer ") else None
    cluster = service.authenticate(db, token)
    if cluster is None:
        raise AppError("Unknown or revoked cluster token.", status=401, code="bad_token")
    return cluster


Cluster = Annotated[K8sCluster, Depends(agent_cluster)]


class Hello(BaseModel):
    agent_version: str = Field(max_length=40)
    kubernetes_version: str | None = Field(default=None, max_length=40)
    provider: str | None = Field(default=None, max_length=40)


class ObjectIn(BaseModel):
    uid: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=253)
    namespace: str | None = Field(default=None, max_length=253)
    data: dict[str, Any] = {}


class SnapshotIn(BaseModel):
    kind: str
    snapshot_id: str = Field(min_length=8, max_length=64)
    final: bool = True
    objects: list[ObjectIn] = Field(default_factory=list, max_length=5000)

    @field_validator("kind")
    @classmethod
    def _kind(cls, value: str) -> str:
        if value not in service.KINDS:
            raise ValueError(f"unsupported kind {value!r}")
        return value


class EventIn(BaseModel):
    uid: str = Field(min_length=1, max_length=64)
    namespace: str | None = Field(default=None, max_length=253)
    object_kind: str | None = Field(default=None, max_length=40)
    object_name: str | None = Field(default=None, max_length=253)
    type: Literal["Normal", "Warning"] = "Normal"
    reason: str = Field(default="", max_length=128)
    message: str = Field(default="", max_length=4000)
    count: int = Field(default=1, ge=1)
    first_at: datetime
    last_at: datetime


class EventsIn(BaseModel):
    events: list[EventIn] = Field(default_factory=list, max_length=5000)


@router.post("/hello")
def hello(body: Hello, cluster: Cluster, db: SessionDep) -> dict[str, Any]:
    cluster.agent_version = body.agent_version
    cluster.kubernetes_version = body.kubernetes_version
    cluster.provider = body.provider
    cluster.last_seen_at, cluster.status = utcnow(), "connected"
    db.flush()
    return service.agent_config(cluster)


@router.post("/snapshot")
def snapshot(body: SnapshotIn, cluster: Cluster, db: SessionDep) -> dict[str, int]:
    return service.ingest_objects(
        db,
        cluster,
        body.kind,
        body.snapshot_id,
        (o.model_dump() for o in body.objects),
        final=body.final,
    )


@router.post("/events")
def events(body: EventsIn, cluster: Cluster, db: SessionDep) -> dict[str, int]:
    return {"received": service.ingest_events(db, cluster, (e.model_dump() for e in body.events))}
