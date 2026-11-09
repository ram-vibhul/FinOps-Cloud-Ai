"""Customer metric queries (PromQL) run by the cluster agent."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from costlens.accounts.roles import Permission
from costlens.api.deps import SessionDep, require
from costlens.auth.web import AuthContext
from costlens.core.errors import AppError, NotFoundError
from costlens.db.base import utcnow
from costlens.k8s.promql import problem
from costlens.models import K8sCluster, K8sMetricPoint, K8sMetricQuery

router = APIRouter(prefix="/api/v1/k8s", tags=["kubernetes"])
Reader = Annotated[AuthContext, require(Permission.READ)]
Admin = Annotated[AuthContext, require(Permission.MANAGE_CONNECTIONS)]
MAX_QUERIES = 10


def _check(promql: str) -> str:
    reason = problem(promql)
    if reason:
        raise ValueError(f"This query cannot be used: {reason}.")
    return promql.strip()


class QueryIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    promql: str = Field(min_length=1, max_length=500)
    unit: str = Field(default="", max_length=20)

    _promql = field_validator("promql")(classmethod(lambda cls, v: _check(v)))


class QueryPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    enabled: bool | None = None


class QueryOut(BaseModel):
    id: uuid.UUID
    name: str
    promql: str
    unit: str
    enabled: bool
    last_run_at: datetime | None
    last_error: str | None
    latest: list[dict[str, Any]] = []


def _query(db: SessionDep, query_id: uuid.UUID) -> K8sMetricQuery:
    q = db.get(K8sMetricQuery, query_id)
    if q is None:
        raise NotFoundError("Query not found.")
    return q


def _latest(db: SessionDep, q: K8sMetricQuery) -> list[dict[str, Any]]:
    at = db.scalar(select(func.max(K8sMetricPoint.at)).where(K8sMetricPoint.query_id == q.id))
    if at is None:
        return []
    rows = db.scalars(
        select(K8sMetricPoint)
        .where(K8sMetricPoint.query_id == q.id, K8sMetricPoint.at == at)
        .order_by(K8sMetricPoint.value.desc())
        .limit(10)
    )
    return [
        {"series": r.series, "labels": r.labels, "value": r.value, "at": r.at.isoformat()}
        for r in rows
    ]


def _out(db: SessionDep, q: K8sMetricQuery) -> QueryOut:
    out = QueryOut.model_validate(q, from_attributes=True)
    out.latest = _latest(db, q)
    return out


@router.get("/clusters/{cluster_id}/metric-queries", response_model=list[QueryOut])
def list_queries(cluster_id: uuid.UUID, auth: Reader, db: SessionDep) -> list[QueryOut]:
    rows = db.scalars(
        select(K8sMetricQuery)
        .where(K8sMetricQuery.cluster_id == cluster_id)
        .order_by(K8sMetricQuery.created_at)
    )
    return [_out(db, q) for q in rows]


@router.post("/clusters/{cluster_id}/metric-queries", response_model=QueryOut, status_code=201)
def create_query(cluster_id: uuid.UUID, body: QueryIn, auth: Admin, db: SessionDep) -> QueryOut:
    if db.get(K8sCluster, cluster_id) is None:
        raise NotFoundError("Cluster not found.")
    count = (
        db.scalar(
            select(func.count())
            .select_from(K8sMetricQuery)
            .where(K8sMetricQuery.cluster_id == cluster_id)
        )
        or 0
    )
    if count >= MAX_QUERIES:
        raise AppError(
            f"A cluster can have at most {MAX_QUERIES} queries.",
            status=409,
            code="too_many_queries",
        )
    q = K8sMetricQuery(
        org_id=auth.org_id,
        cluster_id=cluster_id,
        name=body.name,
        promql=body.promql,
        unit=body.unit,
        enabled=True,
    )
    db.add(q)
    db.flush()
    return _out(db, q)


@router.patch("/metric-queries/{query_id}", response_model=QueryOut)
def update_query(query_id: uuid.UUID, body: QueryPatch, auth: Admin, db: SessionDep) -> QueryOut:
    q = _query(db, query_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(q, field, value)
    db.flush()
    return _out(db, q)


@router.delete("/metric-queries/{query_id}", status_code=204)
def delete_query(query_id: uuid.UUID, auth: Admin, db: SessionDep) -> Response:
    db.delete(_query(db, query_id))
    db.flush()
    return Response(status_code=204)


@router.get("/metric-queries/{query_id}/points")
def query_points(
    query_id: uuid.UUID,
    auth: Reader,
    db: SessionDep,
    hours: Annotated[int, Query(ge=1, le=24 * 30)] = 24,
) -> dict[str, Any]:
    q = _query(db, query_id)
    rows = db.scalars(
        select(K8sMetricPoint)
        .where(
            K8sMetricPoint.query_id == q.id, K8sMetricPoint.at >= utcnow() - timedelta(hours=hours)
        )
        .order_by(K8sMetricPoint.at)
        .limit(20_000)
    )
    series: dict[str, dict[str, Any]] = defaultdict(lambda: {"labels": {}, "points": []})
    for r in rows:
        s = series[r.series]
        s["labels"] = r.labels
        s["points"].append([r.at.isoformat(), r.value])
    ranked = sorted(
        series.items(), key=lambda kv: -(kv[1]["points"][-1][1] if kv[1]["points"] else 0)
    )
    return {"name": q.name, "unit": q.unit, "series": [{"key": k, **v} for k, v in ranked[:20]]}
