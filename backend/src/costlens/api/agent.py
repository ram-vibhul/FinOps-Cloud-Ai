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
    usage_source: Literal["metrics-server", "prometheus"] | None = None
    prometheus_url: str | None = Field(default=None, max_length=300)
    prometheus_discovered: bool = False


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
    if body.usage_source:
        agent_info = {
            "usage_source": body.usage_source,
            "prometheus_url": body.prometheus_url,
            "prometheus_discovered": body.prometheus_discovered,
        }
        if (cluster.settings or {}).get("agent") != agent_info:  # only write on change
            cluster.settings = {**(cluster.settings or {}), "agent": agent_info}
    cluster.last_seen_at, cluster.status = utcnow(), "connected"
    db.flush()
    return service.agent_config(cluster, db)


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
    received = service.ingest_events(db, cluster, (e.model_dump() for e in body.events))
    from costlens.k8s.health import first_scan

    first_scan(db, cluster, utcnow())  # once, after the first full snapshot (events follow it)
    return {"received": received}


class UsageSample(BaseModel):
    namespace: str = Field(max_length=253)
    pod: str = Field(max_length=253)
    container: str = Field(max_length=253)
    cpu: float = Field(ge=0, le=10_000)  # cores
    memory: float = Field(ge=0, le=100_000)  # GiB


class UsageIn(BaseModel):
    sampled_at: datetime | None = None  # ignored if far from now; server time is used then
    samples: list[UsageSample] = Field(default_factory=list, max_length=20_000)


@router.post("/usage")
def usage(body: UsageIn, cluster: Cluster, db: SessionDep) -> dict[str, int]:
    from datetime import timedelta

    from costlens.k8s.usage import ingest_usage

    now = utcnow()
    at = body.sampled_at
    if at is None or at.tzinfo is None or abs(at - now) > timedelta(minutes=10):
        at = now  # clocks drift; never let an agent write into old or future hours
    received = ingest_usage(db, cluster, at, (s.model_dump() for s in body.samples))
    cluster.last_seen_at, cluster.status = now, "connected"
    return {"received": received}


class TaskResult(BaseModel):
    ok: bool
    text: str | None = Field(default=None, max_length=2_000_000)
    error: str | None = Field(default=None, max_length=4000)


@router.post("/tasks/claim")
def claim_tasks(cluster: Cluster, db: SessionDep) -> dict[str, Any]:
    from costlens.k8s.tasks import claim

    return {"tasks": claim(db, cluster)}


@router.post("/tasks/{task_id}/result")
def task_result(task_id: str, body: TaskResult, cluster: Cluster, db: SessionDep) -> dict[str, str]:
    import uuid

    from costlens.k8s.tasks import complete

    try:
        tid = uuid.UUID(task_id)
    except ValueError as exc:
        raise AppError("Unknown task.", status=404, code="unknown_task") from exc
    task = complete(db, cluster, tid, ok=body.ok, text=body.text, error=body.error)
    return {"status": task.status}


@router.post("/otlp/v1/metrics")
async def otlp_metrics(request: Request, cluster: Cluster, db: SessionDep) -> dict[str, Any]:
    """OTLP/HTTP metrics (JSON). Point an OpenTelemetry Collector's ``otlphttp`` exporter
    here with ``encoding: json`` and the cluster token as a Bearer header."""
    import json as jsonlib

    from costlens.k8s.otlp import samples
    from costlens.k8s.usage import ingest_usage

    content_type = request.headers.get("content-type", "")
    if "json" not in content_type:
        raise AppError(
            "Only OTLP JSON is accepted: set encoding: json on the otlphttp exporter.",
            status=415,
            code="otlp_json_only",
        )
    try:
        payload = jsonlib.loads(await request.body() or b"{}")
    except ValueError as exc:
        raise AppError("Body is not JSON.", code="bad_payload") from exc
    found, ignored = samples(payload if isinstance(payload, dict) else {})
    now = utcnow()
    received = ingest_usage(db, cluster, now, found) if found else 0
    cluster.last_seen_at, cluster.status = now, "connected"
    # OTLP response: partial success tells the Collector how many points were dropped.
    if ignored:
        return {
            "partialSuccess": {
                "rejectedDataPoints": str(ignored),
                "errorMessage": "only container CPU and memory metrics are used",
            }
        }
    return {"partialSuccess": {}, "received": received}


class MetricSeries(BaseModel):
    labels: dict[str, str] = Field(default_factory=dict)
    value: float


class MetricsIn(BaseModel):
    query_id: str
    ok: bool = True
    error: str | None = Field(default=None, max_length=500)
    series: list[MetricSeries] = Field(default_factory=list, max_length=50)


@router.post("/metrics")
def metric_results(body: MetricsIn, cluster: Cluster, db: SessionDep) -> dict[str, int]:
    from costlens.k8s.metrics import store

    return {
        "stored": store(
            db, cluster, body.query_id, body.ok, body.error, [s.model_dump() for s in body.series]
        )
    }
