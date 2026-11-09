"""Usage samples from the agent (metrics-server), kept as hourly aggregates per
workload container."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from costlens.k8s.allocation import owner_of
from costlens.k8s.quantity import cores, gib
from costlens.models import K8sCluster, K8sObject, K8sUsage


def _limits(container: dict[str, Any]) -> tuple[float, float, float | None, float | None]:
    res = container.get("resources") or {}
    req, lim = res.get("requests") or {}, res.get("limits") or {}
    return (
        cores(req.get("cpu")),
        gib(req.get("memory")),
        cores(lim["cpu"]) if lim.get("cpu") else None,
        gib(lim["memory"]) if lim.get("memory") else None,
    )


def ingest_usage(
    db: Session, cluster: K8sCluster, sampled_at: datetime, samples: Iterable[dict[str, Any]]
) -> int:
    """Add one round of samples ({namespace, pod, container, cpu, memory}) to the hour."""
    hour = sampled_at.replace(minute=0, second=0, microsecond=0)
    samples = list(samples)
    live = db.scalars(
        select(K8sObject).where(
            K8sObject.cluster_id == cluster.id,
            K8sObject.deleted_at.is_(None),
            K8sObject.kind.in_(("Pod", "ReplicaSet", "Job")),
        )
    ).all()
    by_uid = {(o.kind, o.uid): o for o in live}
    pods = {(o.namespace, o.name): o for o in live if o.kind == "Pod"}
    grouped: dict[tuple[str, str, str, str], dict[str, Any]] = defaultdict(
        lambda: {"n": 0, "cpu": [], "mem": [], "spec": None, "pods": set()}
    )
    for s in samples:
        pod = pods.get((s.get("namespace"), str(s.get("pod") or "")))
        if pod is None:  # pod not reported yet (next snapshot will have it)
            continue
        kind, name = owner_of(pod, by_uid)
        g = grouped[(pod.namespace or "", kind, name, s["container"])]
        g["cpu"].append(max(0.0, float(s.get("cpu") or 0)))
        g["mem"].append(max(0.0, float(s.get("memory") or 0)))
        g["pods"].add(pod.name)
        if g["spec"] is None:
            spec = next(
                (
                    c
                    for c in (pod.data or {}).get("containers") or []
                    if c.get("name") == s["container"]
                ),
                None,
            )
            g["spec"] = _limits(spec) if spec else (0.0, 0.0, None, None)
    if not grouped:
        return 0
    existing = {
        (r.namespace, r.kind, r.name, r.container): r
        for r in db.scalars(
            select(K8sUsage).where(K8sUsage.cluster_id == cluster.id, K8sUsage.hour == hour)
        )
    }
    for key, g in grouped.items():
        row = existing.get(key)
        if row is None:
            row = K8sUsage(
                org_id=cluster.org_id,
                cluster_id=cluster.id,
                hour=hour,
                namespace=key[0],
                kind=key[1],
                name=key[2],
                container=key[3],
                samples=0,
                cpu_sum=0.0,
                cpu_max=0.0,
                memory_sum=0.0,
                memory_max=0.0,
                replicas=0,
            )
            db.add(row)
        row.samples += len(g["cpu"])
        row.cpu_sum += sum(g["cpu"])
        row.memory_sum += sum(g["mem"])
        row.cpu_max = max(row.cpu_max, *g["cpu"])
        row.memory_max = max(row.memory_max, *g["mem"])
        row.replicas = max(row.replicas, len(g["pods"]))
        row.cpu_request, row.memory_request, row.cpu_limit, row.memory_limit = g["spec"]
    db.flush()
    return sum(len(g["cpu"]) for g in grouped.values())
