"""Right-sizing from observed usage: requests never go below the observed peak plus
headroom. Savings are the share of the workload's actual allocated cost."""

from __future__ import annotations

import math
import uuid
from collections import defaultdict
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ccradar.costs.fx import converted
from ccradar.db.base import utcnow
from ccradar.k8s.allocation import DEFAULT_CPU_HOUR, DEFAULT_GIB_HOUR
from ccradar.models import K8sCluster, K8sCost, K8sUsage

HEADROOM = 0.20
MIN_HOURS = 24
MIN_CHANGE = 0.20  # suggest a decrease only when it is at least 20%
OOM_MARGIN = 0.10  # memory peak within 10% of the limit
CPU_STEP = 0.01  # 10m
MEM_STEP = 1 / 64  # 16 MiB
DAYS_PER_MONTH = 30.4


def _up(value: float, step: float) -> float:
    return round(math.ceil(value / step - 1e-9) * step, 4)


def _weight(cpu: float, mem: float) -> float:
    return cpu * DEFAULT_CPU_HOUR + mem * DEFAULT_GIB_HOUR


def _aggregate(rows: list[K8sUsage]) -> dict[tuple, dict[str, Any]]:
    out: dict[tuple, dict[str, Any]] = {}
    for r in sorted(rows, key=lambda x: x.hour):
        key = (r.cluster_id, r.namespace, r.kind, r.name, r.container)
        a = out.setdefault(
            key,
            {
                "samples": 0,
                "cpu_sum": 0.0,
                "mem_sum": 0.0,
                "cpu_peak": 0.0,
                "mem_peak": 0.0,
                "hours": 0,
                "replicas": 0,
            },
        )
        a["samples"] += r.samples
        a["cpu_sum"] += r.cpu_sum
        a["mem_sum"] += r.memory_sum
        a["cpu_peak"] = max(a["cpu_peak"], r.cpu_max)
        a["mem_peak"] = max(a["mem_peak"], r.memory_max)
        a["hours"] += 1
        a["replicas"] = r.replicas  # latest
        # requests/limits in force now (latest hour wins)
        a.update(
            cpu_request=r.cpu_request,
            mem_request=r.memory_request,
            cpu_limit=r.cpu_limit,
            mem_limit=r.memory_limit,
        )
    return out


def _advise(a: dict[str, Any], headroom: float = HEADROOM) -> dict[str, Any]:
    cpu_rec = max(CPU_STEP, _up(a["cpu_peak"] * (1 + headroom), CPU_STEP))
    mem_rec = max(MEM_STEP * 4, _up(a["mem_peak"] * (1 + headroom), MEM_STEP))
    cpu_req, mem_req = a["cpu_request"], a["mem_request"]
    oom = bool(a["mem_limit"]) and a["mem_peak"] >= a["mem_limit"] * (1 - OOM_MARGIN)
    if a["hours"] < MIN_HOURS:
        action, new_cpu, new_mem = "learning", cpu_req, mem_req
    elif cpu_req == 0 and mem_req == 0:
        action, new_cpu, new_mem = "set_requests", cpu_rec, mem_rec
    elif a["cpu_peak"] > cpu_req or a["mem_peak"] > mem_req:
        action = "increase"
        new_cpu, new_mem = max(cpu_req, cpu_rec), max(mem_req, mem_rec)
    else:
        new_cpu = cpu_rec if cpu_rec <= cpu_req * (1 - MIN_CHANGE) else cpu_req
        new_mem = mem_rec if mem_rec <= mem_req * (1 - MIN_CHANGE) else mem_req
        action = "decrease" if (new_cpu, new_mem) != (cpu_req, mem_req) else "ok"
    return {
        "action": action,
        "oom_risk": oom,
        "recommended_cpu": round(new_cpu, 4),
        "recommended_memory_gib": round(new_mem, 4),
        "recommended_memory_limit_gib": max(a["mem_limit"] or 0, mem_rec)
        if oom
        else a["mem_limit"],
    }


def recommendations(
    db: Session,
    org_id: uuid.UUID,
    currency: str,
    *,
    cluster_id: uuid.UUID | None = None,
    days: int = 14,
) -> dict[str, Any]:
    since = utcnow() - timedelta(days=days)
    q = select(K8sUsage).where(K8sUsage.org_id == org_id, K8sUsage.hour >= since)
    if cluster_id:
        q = q.where(K8sUsage.cluster_id == cluster_id)
    stats = _aggregate(list(db.scalars(q)))
    # Monthly allocated cost per workload (same window).
    t = K8sCost
    amount = converted(t.cost, t.currency, func.date(t.hour), currency, t.org_id)
    cost_q = (
        select(
            t.cluster_id,
            t.namespace,
            t.kind,
            t.name,
            func.sum(amount),
            func.count(func.distinct(t.hour)),
        )
        .where(t.org_id == org_id, t.hour >= since, t.kind != "Idle")
        .group_by(t.cluster_id, t.namespace, t.kind, t.name)
    )
    if cluster_id:
        cost_q = cost_q.where(t.cluster_id == cluster_id)
    monthly = {
        (c, ns, k, n): float(total or 0) / max(hours, 1) * 24 * DAYS_PER_MONTH
        for c, ns, k, n, total, hours in db.execute(cost_q)
    }
    names = dict(db.execute(select(K8sCluster.id, K8sCluster.name)).all())
    old_weights: dict[tuple, float] = defaultdict(float)
    for (c, ns, k, n, _), a in stats.items():
        old_weights[(c, ns, k, n)] += _weight(a["cpu_request"], a["mem_request"])
    from ccradar.findings.targets import Targets  # avoid an import cycle

    targets = Targets(db)
    items = []
    for (c, ns, k, n, container), a in stats.items():
        eff = targets.for_workload(str(c), ns, n)
        advice = _advise(a, eff.headroom_pct / 100)
        wl = (c, ns, k, n)
        cost = monthly.get(wl, 0.0)
        old_w = old_weights[wl]
        delta = _weight(a["cpu_request"], a["mem_request"]) - _weight(
            advice["recommended_cpu"], advice["recommended_memory_gib"]
        )
        saving = cost * delta / old_w if old_w else 0.0
        samples = a["samples"] or 1
        items.append(
            {
                "cluster": names.get(c),
                "cluster_id": str(c),
                "namespace": ns,
                "kind": k,
                "workload": n,
                "container": container,
                "replicas": a["replicas"],
                "hours": a["hours"],
                "cpu_request": a["cpu_request"],
                "memory_request_gib": round(a["mem_request"], 4),
                "cpu_limit": a["cpu_limit"],
                "memory_limit_gib": a["mem_limit"],
                "cpu_avg": round(a["cpu_sum"] / samples, 4),
                "cpu_peak": round(a["cpu_peak"], 4),
                "memory_avg_gib": round(a["mem_sum"] / samples, 4),
                "memory_peak_gib": round(a["mem_peak"], 4),
                "workload_monthly_cost": round(cost, 2),
                "monthly_saving": round(saving, 2)
                if advice["action"] in ("decrease", "increase")
                else 0.0,
                "headroom_pct": round(eff.headroom_pct),
                "target": eff.name,
                "min_replicas": eff.min_replicas,
                "replicas_below_target": a["replicas"] < eff.min_replicas,
                **advice,
            }
        )
    order = {"increase": 0, "decrease": 1, "set_requests": 2, "ok": 3, "learning": 4}
    items.sort(key=lambda i: (not i["oom_risk"], order[i["action"]], -i["monthly_saving"]))
    saving = sum(i["monthly_saving"] for i in items if i["action"] == "decrease")
    return {
        "currency": currency,
        "headroom_pct": int(HEADROOM * 100),
        "days": days,
        "monthly_saving": round(saving, 2),
        "items": items,
    }
