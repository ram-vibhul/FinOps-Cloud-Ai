"""Requests vs usage and right-sizing recommendations."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Query
from sqlalchemy import select

from costlens.accounts.roles import Permission
from costlens.api.deps import SessionDep, require
from costlens.auth.web import AuthContext
from costlens.db.base import utcnow
from costlens.k8s.rightsizing import recommendations
from costlens.models import K8sUsage, Organization

router = APIRouter(prefix="/api/v1/k8s", tags=["kubernetes"])
Reader = Annotated[AuthContext, require(Permission.READ)]


@router.get("/rightsizing")
def rightsizing(
    auth: Reader,
    db: SessionDep,
    cluster_id: Annotated[uuid.UUID | None, Query()] = None,
    days: Annotated[int, Query(ge=1, le=90)] = 14,
    currency: Annotated[str | None, Query(pattern=r"^[A-Z]{3}$")] = None,
) -> dict[str, Any]:
    org = db.get(Organization, auth.org_id)
    assert auth.org_id is not None  # noqa: S101
    display = currency or (org.default_currency if org else "USD")
    return recommendations(db, auth.org_id, display, cluster_id=cluster_id, days=days)


@router.get("/usage")
def usage_series(
    auth: Reader,
    db: SessionDep,
    cluster_id: Annotated[uuid.UUID, Query()],
    namespace: Annotated[str, Query(max_length=253)],
    kind: Annotated[str, Query(max_length=40)],
    name: Annotated[str, Query(max_length=253)],
    days: Annotated[int, Query(ge=1, le=90)] = 14,
) -> dict[str, Any]:
    """Hourly usage vs requests for one workload, per container."""
    rows = db.scalars(
        select(K8sUsage)
        .where(
            K8sUsage.cluster_id == cluster_id,
            K8sUsage.namespace == namespace,
            K8sUsage.kind == kind,
            K8sUsage.name == name,
            K8sUsage.hour >= utcnow() - timedelta(days=days),
        )
        .order_by(K8sUsage.hour)
    ).all()
    containers: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        n = r.samples or 1
        containers.setdefault(r.container, []).append(
            {
                "hour": r.hour.isoformat(),
                "cpu_avg": round(r.cpu_sum / n, 4),
                "cpu_max": r.cpu_max,
                "cpu_request": r.cpu_request,
                "memory_avg_gib": round(r.memory_sum / n, 4),
                "memory_max_gib": r.memory_max,
                "memory_request_gib": r.memory_request,
                "memory_limit_gib": r.memory_limit,
                "replicas": r.replicas,
            }
        )
    return {"containers": containers}
