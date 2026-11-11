"""Demo Kubernetes cluster: workloads, some idle capacity, 30 days of allocated cost."""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, time, timedelta
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from ccradar.db.base import Base
from ccradar.demo.seed import DemoContext, demo_seeder
from ccradar.k8s.allocation import allocate
from ccradar.k8s.service import ingest_events, ingest_objects
from ccradar.models import K8sCluster, K8sCost

DAYS = 30
NODES = 6
DEPLOY_HOURS_AGO = 20  # nightly-etl v3.2: its memory use rises after this deploy


def _pods(
    name: str,
    ns: str,
    owner: tuple[str, str, str],
    n: int,
    cpu: str,
    mem: str,
    nodes: list[str],
    start: int,
    phase: str = "Running",
    restarts: int = 0,
    waiting: str | None = None,
) -> list[dict[str, Any]]:
    kind, oname, ouid = owner
    out = []
    for i in range(n):
        status: dict[str, Any] = {"name": name, "ready": waiting is None, "restartCount": restarts}
        if waiting:
            status["current"] = {"state": "waiting", "reason": waiting}
            status["last"] = {"state": "terminated", "reason": "OOMKilled", "exitCode": 137}
        out.append(
            {
                "uid": f"pod-{ns}-{name}-{i}",
                "name": f"{oname}-{i:x}{i * 7 % 10}k",
                "namespace": ns,
                "data": {
                    "owners": [{"kind": kind, "name": oname, "uid": ouid}],
                    "nodeName": nodes[(start + i) % len(nodes)],
                    "phase": phase,
                    "containers": [
                        {
                            "name": name,
                            "image": f"acme/{name}:2.4.1",
                            "resources": {"requests": {"cpu": cpu, "memory": mem}},
                        }
                    ],
                    "container_statuses": [status],
                },
            }
        )
    return out


@demo_seeder("k8s.cluster", order=40)
def seed_cluster(db: Session, ctx: DemoContext) -> None:
    now = datetime.combine(ctx.today, time(0), UTC) - timedelta(hours=1)
    cluster = K8sCluster(
        id=uuid.uuid4(),
        org_id=ctx.org_id,
        name="prod-ap-south-1",
        token_hash=secrets.token_hex(32),  # no token exists for the demo
        token_hint="demo",  # noqa: S106 - a label, not a secret
        status="connected",
        agent_version="0.1.0",
        kubernetes_version="v1.33.4-eks",
        provider="eks",
        node_count=NODES,
        last_seen_at=now,
    )
    db.add(cluster)
    db.flush()
    nodes = [f"ip-10-20-{i}-{40 + i * 3}.ap-south-1.compute.internal" for i in range(NODES)]
    res = {"cpu": "8", "memory": "32Gi"}
    ingest_objects(
        db,
        cluster,
        "Node",
        "demo",
        [
            {
                "uid": f"node-{i}",
                "name": n,
                "data": {
                    "providerID": f"aws:///ap-south-1{'abc'[i % 3]}/i-0demo{i:011d}",
                    "labels": {
                        "node.kubernetes.io/instance-type": "m6i.2xlarge",
                        "topology.kubernetes.io/zone": f"ap-south-1{'abc'[i % 3]}",
                    },
                    "capacity": res,
                    "allocatable": {"cpu": "7910m", "memory": "30Gi"},
                    "info": {"kubeletVersion": "v1.33.4-eks"},
                },
            }
            for i, n in enumerate(nodes)
        ],
        final=True,
    )
    workloads = [
        ("Deployment", "checkout", "checkout-api", 6, "1", "2Gi"),
        ("Deployment", "checkout", "checkout-worker", 3, "500m", "1Gi"),
        ("Deployment", "payments", "payments-gateway", 4, "2", "4Gi"),  # far above its use
        ("StatefulSet", "search", "opensearch", 3, "2", "8Gi"),
        ("Deployment", "web", "storefront", 4, "500m", "1Gi"),
        ("DaemonSet", "monitoring", "node-exporter", NODES, "100m", "128Mi"),
        ("Deployment", "monitoring", "prometheus", 1, "1", "6Gi"),
    ]
    pods: list[dict[str, Any]] = []
    owners = []
    for i, (kind, ns, name, n, cpu, mem) in enumerate(workloads):
        uid = f"wl-{ns}-{name}"
        owners.append(
            {
                "uid": uid,
                "name": name,
                "namespace": ns,
                "kind": kind,
                "data": {"replicas": n, "readyReplicas": n},
            }
        )
        pods += _pods(name, ns, (kind, name, uid), n, cpu, mem, nodes, i)
    # A crash-looping job (for health checks): CronJob -> Job -> Pod.
    owners.append(
        {
            "uid": "wl-data-etl",
            "name": "nightly-etl",
            "namespace": "data",
            "kind": "CronJob",
            "data": {"schedule": "0 2 * * *"},
        }
    )
    jobs = [
        {
            "uid": "job-etl",
            "name": "nightly-etl-29384",
            "namespace": "data",
            "data": {"owners": [{"kind": "CronJob", "name": "nightly-etl", "uid": "wl-data-etl"}]},
        }
    ]
    pods += _pods(
        "etl",
        "data",
        ("Job", "nightly-etl-29384", "job-etl"),
        1,
        "1",
        "3Gi",
        nodes,
        2,
        restarts=14,
        waiting="CrashLoopBackOff",
    )
    for kind in ("Deployment", "StatefulSet", "DaemonSet", "CronJob"):
        ingest_objects(
            db,
            cluster,
            kind,
            "demo",
            [{k: v for k, v in o.items() if k != "kind"} for o in owners if o["kind"] == kind],
            final=True,
        )
    ingest_objects(db, cluster, "Job", "demo", jobs, final=True)
    ingest_objects(db, cluster, "Pod", "demo", pods, final=True)
    ingest_objects(
        db,
        cluster,
        "Namespace",
        "demo",
        [
            {"uid": f"ns-{ns}", "name": ns, "data": {"phase": "Active"}}
            for ns in ("checkout", "payments", "search", "web", "monitoring", "data")
        ],
        final=True,
    )
    ingest_events(
        db,
        cluster,
        [
            {
                "uid": "ev-etl-backoff",
                "namespace": "data",
                "object_kind": "Pod",
                "object_name": "nightly-etl-29384-0",
                "type": "Warning",
                "reason": "BackOff",
                "message": "Back-off restarting failed container etl in pod nightly-etl-29384-0",
                "count": 14,
                "first_at": now - timedelta(hours=3),
                "last_at": now,
            },
            {
                "uid": "ev-etl-oom",
                "namespace": "data",
                "object_kind": "Pod",
                "object_name": "nightly-etl-29384-0",
                "type": "Warning",
                "reason": "OOMKilling",
                "message": "Memory cgroup out of memory: Killed process (python)",
                "count": 14,
                "first_at": now - timedelta(hours=3),
                "last_at": now - timedelta(minutes=4),
            },
        ],
    )
    from ccradar.changes.service import ChangeIn, record

    record(
        db,
        ctx.org_id,
        ChangeIn(
            occurred_at=now - timedelta(hours=DEPLOY_HOURS_AGO),
            kind="deployment",
            title='acme/nightly-etl: workflow "Deploy to production" succeeded on main (4f2a9c1)',
            environment="production",
            actor="priya",
            external_id="workflow_run:demo-etl-32",
            url="https://github.com/acme/nightly-etl/actions/runs/1",
            attributes={
                "repository": "acme/nightly-etl",
                "workflow": "Deploy to production",
                "conclusion": "success",
                "note": "v3.2 loads the whole day's file into memory",
            },
        ),
        source="github",
    )
    cluster.last_seen_at = now  # the demo's "latest report" is its snapshot time
    # One real allocation, then the same shape for every hour of the last 30 days
    # (busier on weekdays) so charts have history.
    allocate(db, cluster, now)
    base = [
        {
            c: getattr(r, c)
            for c in (
                "namespace",
                "kind",
                "name",
                "node",
                "cpu_request",
                "memory_request",
                "cost",
                "currency",
                "source",
            )
        }
        for r in db.scalars(select(K8sCost).where(K8sCost.cluster_id == cluster.id))
    ]
    rows = []
    for h in range(1, DAYS * 24):
        hour = now - timedelta(hours=h)
        busy = 1.0 if hour.weekday() < 5 else 0.85
        for r in base:
            factor = (2 - busy) if r["kind"] == "Idle" else busy
            rows.append(
                {
                    **r,
                    "id": uuid.uuid4(),
                    "org_id": ctx.org_id,
                    "cluster_id": cluster.id,
                    "hour": hour,
                    "cost": float(r["cost"]) * factor,
                }
            )
    table = Base.metadata.tables["k8s_costs"]
    for i in range(0, len(rows), 5000):
        db.execute(insert(table), rows[i : i + 5000])
    db.flush()


# Usage profile per workload container: (cpu avg, cpu peak, memory avg GiB, memory peak GiB).
USAGE = {
    ("checkout", "Deployment", "checkout-api", "checkout-api"): (0.35, 0.55, 1.1, 1.3),
    ("checkout", "Deployment", "checkout-worker", "checkout-worker"): (0.08, 0.18, 0.35, 0.45),
    ("payments", "Deployment", "payments-gateway", "payments-gateway"): (0.12, 0.28, 0.6, 0.85),
    ("search", "StatefulSet", "opensearch", "opensearch"): (1.2, 1.75, 6.4, 7.1),
    ("web", "Deployment", "storefront", "storefront"): (0.42, 0.71, 0.55, 0.8),
    ("monitoring", "DaemonSet", "node-exporter", "node-exporter"): (0.02, 0.05, 0.04, 0.06),
    ("monitoring", "Deployment", "prometheus", "prometheus"): (0.55, 0.85, 4.2, 5.1),
    ("data", "CronJob", "nightly-etl", "etl"): (0.6, 0.95, 2.1, 2.95),
}
REQUESTS = {  # per replica: cpu, memory GiB, memory limit, replicas
    "checkout-api": (1.0, 2.0, None, 6),
    "checkout-worker": (0.5, 1.0, None, 3),
    "payments-gateway": (2.0, 4.0, None, 4),
    "opensearch": (2.0, 8.0, None, 3),
    "storefront": (0.5, 1.0, None, 4),
    "node-exporter": (0.1, 0.125, None, NODES),
    "prometheus": (1.0, 6.0, None, 1),
    "nightly-etl": (1.0, 3.0, 3.0, 1),
}


@demo_seeder("k8s.usage", order=41)
def seed_usage(db: Session, ctx: DemoContext) -> None:
    """14 days of hourly usage, busier in working hours, peaking once a day."""
    import math

    cluster = db.scalar(select(K8sCluster).where(K8sCluster.org_id == ctx.org_id))
    if cluster is None:
        return
    end = datetime.combine(ctx.today, time(0), UTC) - timedelta(hours=1)
    rows = []
    for (ns, kind, name, container), (cpu_avg, cpu_peak, mem_avg, mem_peak) in USAGE.items():
        cpu_req, mem_req, mem_lim, replicas = REQUESTS[name]
        for h in range(14 * 24):
            hour = end - timedelta(hours=h)
            wave = 0.5 + 0.5 * math.sin((hour.hour - 8) / 24 * 2 * math.pi)  # 0..1, top ~14:00
            busy = 1.0 if hour.weekday() < 5 else 0.7
            cpu_now = cpu_avg * (0.6 + 0.8 * wave) * busy
            mem_now = mem_avg * (0.9 + 0.2 * wave)
            before_deploy = name == "nightly-etl" and h > DEPLOY_HOURS_AGO
            if before_deploy:  # v3.1 used about half the memory
                mem_now *= 0.55
            top = h == (10 if name == "nightly-etl" else 24 * 3 + 10)  # one real peak
            samples = 60 * replicas
            rows.append(
                {
                    "id": uuid.uuid4(),
                    "org_id": ctx.org_id,
                    "cluster_id": cluster.id,
                    "hour": hour,
                    "namespace": ns,
                    "kind": kind,
                    "name": name,
                    "container": container,
                    "samples": samples,
                    "cpu_sum": cpu_now * samples,
                    "memory_sum": mem_now * samples,
                    "cpu_max": cpu_peak if top else min(cpu_peak * 0.95, cpu_now * 1.25),
                    "memory_max": (mem_now * 1.08)
                    if before_deploy
                    else (mem_peak if top else min(mem_peak * 0.97, mem_now * 1.08)),
                    "cpu_request": cpu_req,
                    "memory_request": mem_req,
                    "cpu_limit": None,
                    "memory_limit": mem_lim,
                    "replicas": replicas,
                }
            )
    table = Base.metadata.tables["k8s_usage"]
    for i in range(0, len(rows), 5000):
        db.execute(insert(table), rows[i : i + 5000])
    db.flush()
