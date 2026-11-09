"""Kubernetes cost: totals, allocated vs idle per day, and breakdowns."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from sqlalchemy import case, func, select

from costlens.accounts.roles import Permission
from costlens.api.deps import SessionDep, require
from costlens.auth.web import AuthContext
from costlens.costs.fx import converted
from costlens.models import K8sCluster, K8sCost, Organization

router = APIRouter(prefix="/api/v1/k8s", tags=["kubernetes"])
Reader = Annotated[AuthContext, require(Permission.READ)]
IDLE = "Idle"


@router.get("/costs")
def k8s_costs(
    auth: Reader,
    db: SessionDep,
    group_by: Annotated[Literal["cluster", "namespace", "workload", "node"], Query()] = "namespace",
    days: Annotated[int, Query(ge=1, le=400)] = 30,
    cluster_id: Annotated[uuid.UUID | None, Query()] = None,
    namespace: Annotated[str | None, Query(max_length=253)] = None,
    currency: Annotated[str | None, Query(pattern=r"^[A-Z]{3}$")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    org = db.get(Organization, auth.org_id)
    display = currency or (org.default_currency if org else "USD")
    t = K8sCost
    day = func.date(t.hour)
    amount = converted(t.cost, t.currency, day, display, t.org_id)
    latest = db.scalar(select(func.max(t.hour)).where(t.org_id == auth.org_id))
    names = dict(db.execute(select(K8sCluster.id, K8sCluster.name)).all())
    empty = {
        "currency": display,
        "total": 0.0,
        "allocated": 0.0,
        "idle": 0.0,
        "idle_pct": None,
        "estimated_pct": None,
        "hours": 0,
        "daily": [],
        "items": [],
        "clusters": [{"id": str(k), "name": v} for k, v in names.items()],
    }
    if latest is None:
        return empty
    start = latest - timedelta(days=days) + timedelta(hours=1)
    where = [t.org_id == auth.org_id, t.hour >= start, t.hour <= latest]
    if cluster_id:
        where.append(t.cluster_id == cluster_id)
    if namespace:
        where.append(t.namespace == namespace)
    is_idle = t.kind == IDLE
    idle_amount = case((is_idle, amount), else_=0)
    estimate_amount = case((t.source == "estimate", amount), else_=0)
    total, idle, estimated, hours = db.execute(
        select(
            func.sum(amount),
            func.sum(idle_amount),
            func.sum(estimate_amount),
            func.count(func.distinct(t.hour)),
        ).where(*where)
    ).one()
    total, idle, estimated = float(total or 0), float(idle or 0), float(estimated or 0)
    hours = int(hours or 0) or 1
    daily = [
        {
            "day": str(d),
            "allocated": round(float(a or 0) - float(i or 0), 2),
            "idle": round(float(i or 0), 2),
        }
        for d, a, i in db.execute(
            select(day, func.sum(amount), func.sum(idle_amount))
            .where(*where)
            .group_by(day)
            .order_by(day)
        )
    ]
    keys: list[Any]
    if group_by == "cluster":
        keys = [t.cluster_id]
    elif group_by == "namespace":
        keys = [t.namespace]
    elif group_by == "workload":
        keys = [t.namespace, t.kind, t.name]
        where.append(~is_idle)
    else:  # idle capacity by node
        keys = [t.cluster_id, t.name]
        where.append(is_idle)
    rows = db.execute(
        select(
            *keys,
            func.sum(amount),
            func.sum(t.cpu_request),
            func.sum(t.memory_request),
            func.sum(estimate_amount),
        )
        .where(*where)
        .group_by(*keys)
        .order_by(func.sum(amount).desc())
        .limit(limit)
    ).all()
    items = []
    for row in rows:
        *key, cost, cpu, mem, est = row
        cost = float(cost or 0)
        item: dict[str, Any] = {
            "cost": round(cost, 2),
            "share_pct": round(cost / total * 100, 1) if total else None,
            # average over the window; for idle rows this is the free capacity
            "cpu": round(float(cpu or 0) / hours, 3),
            "memory_gib": round(float(mem or 0) / hours, 2),
            "estimated": bool(est) and float(est) >= cost * 0.5,
        }
        if group_by == "cluster":
            item.update(key=str(key[0]), label=names.get(key[0], str(key[0])))
        elif group_by == "namespace":
            item.update(key=key[0] or IDLE, label=key[0] or "Idle capacity", idle=key[0] is None)
        elif group_by == "workload":
            item.update(
                key=f"{key[0]}/{key[1]}/{key[2]}", namespace=key[0], kind=key[1], label=key[2]
            )
        else:
            item.update(key=f"{key[0]}/{key[1]}", cluster=names.get(key[0]), label=key[1])
        items.append(item)
    return {
        **empty,
        "total": round(total, 2),
        "allocated": round(total - idle, 2),
        "idle": round(idle, 2),
        "idle_pct": round(idle / total * 100, 1) if total else None,
        "estimated_pct": round(estimated / total * 100, 1) if total else None,
        "hours": hours,
        "start": start.isoformat(),
        "end": latest.isoformat(),
        "daily": daily,
        "items": items,
    }
