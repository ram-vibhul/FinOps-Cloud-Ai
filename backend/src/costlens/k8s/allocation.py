"""Hourly Kubernetes cost allocation.

For each node: its cost per hour comes from the bill (CUR lines for the node's
instance ID, averaged over the last 7 billed days) or, when the bill has no line
for it, an estimate from per-vCPU and per-GiB rates. The node's cost is split into a
CPU part and a memory part in proportion to those rates; every running pod gets the
share of each part that its *requests* take of the node's allocatable capacity.
Whatever is left is the node's idle cost. Pods roll up to their workload
(ReplicaSet -> Deployment, Job -> CronJob).

Usage-based allocation (requests vs actual use) comes with 3.3.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from costlens.costs import fx
from costlens.costs.fx import converted
from costlens.db.base import Base
from costlens.k8s.quantity import cores, gib
from costlens.models import K8sCluster, K8sObject, Organization

# Public list prices for general-purpose capacity (USD per hour), used only when the
# bill has no line for a node. Overridable per cluster (custom prices: 3.19).
DEFAULT_CPU_HOUR = 0.031611
DEFAULT_GIB_HOUR = 0.004237
BILLING_DAYS = 7
STALE_AFTER = timedelta(hours=2)


@dataclass
class Node:
    name: str
    instance_id: str | None
    cpu: float  # allocatable cores
    memory: float  # allocatable GiB
    capacity_cpu: float
    capacity_memory: float
    spot: bool = False
    cost: float = 0.0
    source: str = "estimate"
    used_cpu: float = 0.0
    used_memory: float = 0.0
    pods: list[dict[str, Any]] = field(default_factory=list)


def instance_id(provider_id: str | None) -> str | None:
    if provider_id and provider_id.startswith("aws://"):
        last = provider_id.rsplit("/", 1)[-1]
        return last if last.startswith("i-") else None
    return None


def price_settings(cluster: K8sCluster) -> dict[str, Any]:
    """The cluster's own prices (3.19) with list-price defaults."""
    p = (cluster.settings or {}).get("prices") or {}
    return {
        "custom": bool(p),
        "currency": p.get("currency", "USD"),
        "cpu_hour": float(p.get("cpu_hour", DEFAULT_CPU_HOUR)),
        "gib_hour": float(p.get("gib_hour", DEFAULT_GIB_HOUR)),
        "node_hour": float(p.get("node_hour", 0.0)),
        "spot_discount_pct": float(p.get("spot_discount_pct", 0.0)),
        "always": bool(p.get("always", False)),  # ignore the bill (e.g. negotiated on-prem rates)
    }


def rates(cluster: K8sCluster) -> tuple[float, float]:
    prices = (cluster.settings or {}).get("prices", {})
    return (
        float(prices.get("cpu_hour", DEFAULT_CPU_HOUR)),
        float(prices.get("gib_hour", DEFAULT_GIB_HOUR)),
    )


def _billed_hourly(
    db: Session, org_id: uuid.UUID, ids: list[str], currency: str, until: date
) -> dict[str, float]:
    """Average hourly cost per instance from the bill (last 7 billed days)."""
    if not ids:
        return {}
    table = Base.metadata.tables["cost_line_items"]
    amount = converted(
        table.c.effective_cost,
        table.c.billing_currency,
        table.c.charge_date,
        currency,
        table.c.org_id,
    )
    rows = db.execute(
        select(
            table.c.resource_id, func.sum(amount), func.count(func.distinct(table.c.charge_date))
        )
        .where(
            table.c.org_id == org_id,
            table.c.resource_id.in_(ids),
            table.c.charge_category == "Usage",
            table.c.charge_date > until - timedelta(days=BILLING_DAYS),
            table.c.charge_date <= until,
        )
        .group_by(table.c.resource_id)
    ).all()
    return {r: float(total) / (days * 24) for r, total, days in rows if total and days}


def owner_of(pod: K8sObject, by_uid: dict[tuple[str, str], K8sObject]) -> tuple[str, str]:
    owners = (pod.data or {}).get("owners") or []
    if not owners:
        return "Pod", pod.name
    kind, name, uid = owners[0].get("kind"), owners[0].get("name"), owners[0].get("uid")
    parent = by_uid.get((kind, uid)) if kind in ("ReplicaSet", "Job") else None
    if parent is not None:
        up = (parent.data or {}).get("owners") or []
        if up and up[0].get("kind") in ("Deployment", "CronJob"):
            return up[0]["kind"], up[0]["name"]
    if kind == "ReplicaSet" and name and "-" in name:  # ReplicaSet not reported (yet)
        return "Deployment", name.rsplit("-", 1)[0]
    return kind or "Pod", name or pod.name


def _requests_of(containers: list[dict[str, Any]]) -> list[tuple[float, float]]:
    out = []
    for c in containers:
        req = (c.get("resources") or {}).get("requests") or {}
        out.append((cores(req.get("cpu")), gib(req.get("memory"))))
    return out


def _load_nodes(live: list[K8sObject]) -> dict[str, Node]:
    nodes: dict[str, Node] = {}
    for o in live:
        if o.kind != "Node":
            continue
        d = o.data or {}
        alloc, cap = d.get("allocatable") or {}, d.get("capacity") or {}
        labels = d.get("labels") or {}
        nodes[o.name] = Node(
            name=o.name,
            instance_id=instance_id(d.get("providerID")),
            cpu=cores(alloc.get("cpu")) or cores(cap.get("cpu")),
            memory=gib(alloc.get("memory")) or gib(cap.get("memory")),
            capacity_cpu=cores(cap.get("cpu")) or cores(alloc.get("cpu")),
            capacity_memory=gib(cap.get("memory")) or gib(alloc.get("memory")),
            spot=labels.get("eks.amazonaws.com/capacityType") == "SPOT"
            or labels.get("karpenter.sh/capacity-type") == "spot"
            or labels.get("cloud.google.com/gke-spot") == "true"
            or labels.get("cloud.google.com/gke-preemptible") == "true"
            or labels.get("kubernetes.azure.com/scalesetpriority") == "spot",
        )
    return nodes


def _price_nodes(
    db: Session, cluster: K8sCluster, nodes: dict[str, Node], currency: str, est_factor: float
) -> None:
    items = Base.metadata.tables["cost_line_items"]
    last_billed = db.scalar(
        select(func.max(items.c.charge_date)).where(items.c.org_id == cluster.org_id)
    )
    ids = [n.instance_id for n in nodes.values() if n.instance_id]
    billed = _billed_hourly(db, cluster.org_id, ids, currency, last_billed) if last_billed else {}
    prices = price_settings(cluster)
    for n in nodes.values():
        if n.instance_id in billed and not prices["always"]:
            n.cost, n.source = billed[n.instance_id], "billing"
            continue
        hourly = (
            n.capacity_cpu * prices["cpu_hour"]
            + n.capacity_memory * prices["gib_hour"]
            + prices["node_hour"]
        )
        if n.spot:
            hourly *= 1 - prices["spot_discount_pct"] / 100
        n.cost = hourly * est_factor
        n.source = "price" if prices["custom"] else "estimate"


def _place_pods(live: list[K8sObject], nodes: dict[str, Node]) -> None:
    by_uid = {(o.kind, o.uid): o for o in live}
    for pod in (o for o in live if o.kind == "Pod"):
        d = pod.data or {}
        node = nodes.get(d.get("nodeName") or "")
        if node is None or d.get("phase") not in ("Running", "Pending"):
            continue
        main = _requests_of(d.get("containers") or [])
        init = _requests_of(d.get("init_containers") or [])
        # Init containers run before the others: the pod reserves the larger amount.
        cpu = max(sum(c for c, _ in main), max((c for c, _ in init), default=0.0))
        mem = max(sum(m for _, m in main), max((m for _, m in init), default=0.0))
        kind, name = owner_of(pod, by_uid)
        node.used_cpu += cpu
        node.used_memory += mem
        node.pods.append({"key": (pod.namespace or "", kind, name), "cpu": cpu, "mem": mem})


def _split(
    n: Node, cpu_rate: float, mem_rate: float, workloads: dict[tuple[str, str, str], dict[str, Any]]
) -> float:
    """Assign the node's cost to its pods' workloads; return what is left (idle)."""
    cpu_weight, mem_weight = n.capacity_cpu * cpu_rate, n.capacity_memory * mem_rate
    total_weight = (cpu_weight + mem_weight) or 1.0
    cpu_part, mem_part = n.cost * cpu_weight / total_weight, n.cost * mem_weight / total_weight
    # Requests can exceed allocatable briefly (or with bad data): scale down, never up.
    cpu_scale = min(1.0, n.cpu / n.used_cpu) if n.used_cpu else 1.0
    mem_scale = min(1.0, n.memory / n.used_memory) if n.used_memory else 1.0
    assigned = 0.0
    for p in n.pods:
        share = cpu_part * p["cpu"] * cpu_scale / n.cpu if n.cpu else 0.0
        share += mem_part * p["mem"] * mem_scale / n.memory if n.memory else 0.0
        w = workloads[p["key"]]
        w["cpu"] += p["cpu"]
        w["mem"] += p["mem"]
        w["cost"] += share
        w["sources"].add(n.source)
        w["nodes"].add(n.name)
        assigned += share
    return max(0.0, n.cost - assigned)


def allocate(db: Session, cluster: K8sCluster, hour: datetime) -> dict[str, float]:
    """Write the allocation for ``hour`` (replacing any earlier run for it)."""
    hour = hour.replace(minute=0, second=0, microsecond=0)
    org = db.get(Organization, cluster.org_id)
    currency = org.default_currency if org else "USD"
    price_currency = price_settings(cluster)["currency"]
    usd = fx.rate(db, hour.date(), price_currency, currency, org_id=cluster.org_id)
    # Without an exchange rate yet, estimates stay in USD (converted when reported).
    est_currency, est_factor = (price_currency, 1.0) if usd is None else (currency, float(usd))
    live = list(
        db.scalars(
            select(K8sObject).where(
                K8sObject.cluster_id == cluster.id,
                K8sObject.deleted_at.is_(None),
                K8sObject.kind.in_(("Node", "Pod", "ReplicaSet", "Job")),
            )
        )
    )
    nodes = _load_nodes(live)
    _price_nodes(db, cluster, nodes, currency, est_factor)
    _place_pods(live, nodes)
    cpu_rate, mem_rate = rates(cluster)
    workloads: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(
        lambda: {"cpu": 0.0, "mem": 0.0, "cost": 0.0, "sources": set(), "nodes": set()}
    )

    def money_currency(source: str) -> str:
        return currency if source == "billing" else est_currency

    rows: list[dict[str, Any]] = []
    idle_total = 0.0
    for n in nodes.values():
        idle = _split(n, cpu_rate, mem_rate, workloads)
        idle_total += idle
        rows.append(
            {
                "namespace": None,
                "kind": "Idle",
                "name": n.name,
                "node": n.name,
                "cpu_request": max(0.0, n.cpu - n.used_cpu),
                "memory_request": max(0.0, n.memory - n.used_memory),
                "cost": round(idle, 6),
                "currency": money_currency(n.source),
                "source": n.source,
            }
        )
    for (namespace, kind, name), w in workloads.items():
        sources = w["sources"]
        source = (
            "billing"
            if sources == {"billing"}
            else "estimate"
            if "estimate" in sources
            else "price"
        )
        rows.append(
            {
                "namespace": namespace,
                "kind": kind,
                "name": name,
                "node": next(iter(w["nodes"])) if len(w["nodes"]) == 1 else None,
                "cpu_request": w["cpu"],
                "memory_request": w["mem"],
                "cost": round(w["cost"], 6),
                "currency": money_currency(source),
                "source": source,
            }
        )
    table = Base.metadata.tables["k8s_costs"]
    db.execute(delete(table).where(table.c.cluster_id == cluster.id, table.c.hour == hour))
    if rows:
        extra = {"org_id": cluster.org_id, "cluster_id": cluster.id, "hour": hour}
        db.execute(insert(table), [{**r, **extra, "id": uuid.uuid4()} for r in rows])
    db.flush()
    allocated = sum(w["cost"] for w in workloads.values())
    return {"allocated": round(allocated, 6), "idle": round(idle_total, 6)}


def due_clusters(db: Session, now: datetime) -> list[K8sCluster]:
    """Clusters whose agent reported recently (silent clusters are not extrapolated)."""
    return [
        c
        for c in db.scalars(select(K8sCluster).where(K8sCluster.last_seen_at.is_not(None)))
        if c.last_seen_at
        and now - (c.last_seen_at if c.last_seen_at.tzinfo else c.last_seen_at.replace(tzinfo=UTC))
        <= STALE_AFTER
    ]
