"""Diagnosis test lab (replay): recorded shapes of real Kubernetes failures are fed
through the real investigation engine, and we score how often the top-ranked cause is
right. Runs offline in CI and with ``costlens lab run``; the live version on a kind
cluster lives in ``tools/k8s-lab``. Each scenario carries realistic noise (healthy
pods, normal events, unrelated warnings) so the checks must pick the right signal."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from costlens.db.base import Base, utcnow

NOW = datetime(2026, 1, 1, 12, tzinfo=UTC)  # fixed: results never depend on the clock
OWNER = {"kind": "ReplicaSet", "name": "app-5d8f", "uid": "rs-app"}


def _pod(
    i: int,
    *,
    current: str | None = None,
    last: str | None = None,
    exit_code: int | None = None,
    restarts: int = 0,
    phase: str = "Running",
    node: str | None = "node-1",
    limit: str | None = None,
) -> dict[str, Any]:
    status: dict[str, Any] = {"name": "app", "ready": current is None, "restartCount": restarts}
    if current:
        status["current"] = {"state": "waiting", "reason": current}
    if last:
        status["last"] = {"state": "terminated", "reason": last, "exitCode": exit_code}
    res: dict[str, Any] = {"requests": {"cpu": "250m", "memory": "256Mi"}}
    if limit:
        res["limits"] = {"memory": limit}
    return {
        "uid": f"pod-{i}",
        "name": f"app-5d8f-{i}",
        "namespace": "shop",
        "data": {
            "owners": [OWNER],
            "nodeName": node if phase != "Pending" else None,
            "phase": phase,
            "containers": [{"name": "app", "image": "acme/app:1.4.2", "resources": res}],
            "container_statuses": [status] if phase != "Pending" else [],
        },
    }


def _event(
    uid: str,
    reason: str,
    message: str,
    *,
    type_: str = "Warning",
    obj: str = "app-5d8f-0",
    count: int = 5,
    kind: str = "Pod",
    ns: str = "shop",
) -> dict[str, Any]:
    return {
        "uid": uid,
        "namespace": ns,
        "object_kind": kind,
        "object_name": obj,
        "type": type_,
        "reason": reason,
        "message": message,
        "count": count,
        "first_at": NOW - timedelta(minutes=50),
        "last_at": NOW - timedelta(minutes=2),
    }


NOISE = [
    _event(
        "noise-scheduled",
        "Scheduled",
        "Successfully assigned shop/app-5d8f-1 to node-2",
        type_="Normal",
        obj="app-5d8f-1",
        count=1,
    ),
    _event(
        "noise-pulled",
        "Pulled",
        'Container image "acme/app:1.4.2" already present on machine',
        type_="Normal",
        obj="app-5d8f-1",
        count=1,
    ),
    # A warning about something else in another namespace must not mislead the checks.
    _event(
        "noise-other",
        "Unhealthy",
        "Readiness probe failed: HTTP probe failed with statuscode: 503",
        obj="billing-7c-0",
        ns="billing",
        count=2,
    ),
]
HEALTHY_NODE = {
    "uid": "node-1",
    "name": "node-1",
    "data": {
        "conditions": [
            {"type": "Ready", "status": "True"},
            {"type": "MemoryPressure", "status": "False"},
        ]
    },
}


@dataclass
class Scenario:
    name: str
    expected: str | None  # the check that should rank first; None = nothing wrong
    pods: list[dict[str, Any]]
    events: list[dict[str, Any]] = field(default_factory=list)
    nodes: list[dict[str, Any]] = field(default_factory=lambda: [HEALTHY_NODE])
    usage: dict[str, float] | None = None
    description: str = ""


SCENARIOS: list[Scenario] = [
    Scenario(
        "oom-kill",
        "oom",
        [
            _pod(
                0,
                current="CrashLoopBackOff",
                last="OOMKilled",
                exit_code=137,
                restarts=11,
                limit="256Mi",
            ),
            _pod(1),
        ],
        [
            _event("e1", "BackOff", "Back-off restarting failed container app in pod app-5d8f-0"),
            _event("e2", "OOMKilling", "Memory cgroup out of memory: Killed process 4242 (java)"),
        ],
        usage={"mem": 0.25, "mem_limit": 0.25},
        description="Heap grows past the memory limit",
    ),
    Scenario(
        "crash-loop-bad-flag",
        "crash_loop",
        [_pod(0, current="CrashLoopBackOff", last="Error", exit_code=2, restarts=7), _pod(1)],
        [_event("e1", "BackOff", "Back-off restarting failed container app in pod app-5d8f-0")],
        description="A new command-line flag the binary does not know",
    ),
    Scenario(
        "missing-binary",
        "crash_loop",
        [_pod(0, current="CrashLoopBackOff", last="ContainerCannotRun", exit_code=127, restarts=5)],
        [_event("e1", "BackOff", "Back-off restarting failed container app")],
        description="Entrypoint points at a file not in the image",
    ),
    Scenario(
        "bad-config",
        "config",
        [_pod(0, current="CreateContainerConfigError"), _pod(1)],
        [_event("e1", "Failed", 'Error: configmap "app-settings" not found')],
        description="Deployment refers to a ConfigMap that was never created",
    ),
    Scenario(
        "image-pull-auth",
        "image_pull",
        [_pod(0, current="ImagePullBackOff")],
        [
            _event(
                "e1",
                "Failed",
                'Failed to pull image "registry.acme.example/app:1.5.0": '
                "unauthorized: authentication required",
            ),
            _event("e2", "BackOff", 'Back-off pulling image "registry.acme.example/app:1.5.0"'),
        ],
        description="Private registry without imagePullSecrets",
    ),
    Scenario(
        "image-tag-typo",
        "image_pull",
        [_pod(0, current="ErrImagePull")],
        [_event("e1", "Failed", 'Failed to pull image "acme/app:1.4.20": manifest unknown')],
        description="Tag that does not exist",
    ),
    Scenario(
        "liveness-probe",
        "probes",
        [_pod(0, restarts=4), _pod(1)],
        [
            _event(
                "e1",
                "Unhealthy",
                'Liveness probe failed: Get "http://10.0.1.7:8080/healthz": '
                "context deadline exceeded",
            ),
            _event(
                "e2",
                "Killing",
                "Container app failed liveness probe, will be restarted",
                type_="Normal",
            ),
        ],
        description="Probe timeout shorter than a slow start",
    ),
    Scenario(
        "node-memory-pressure",
        "node",
        [_pod(0), _pod(1)],
        [_event("e1", "Evicted", "The node was low on resource: memory.", count=2)],
        nodes=[
            {
                "uid": "node-1",
                "name": "node-1",
                "data": {
                    "conditions": [
                        {"type": "Ready", "status": "True"},
                        {"type": "MemoryPressure", "status": "True"},
                    ]
                },
            }
        ],
        description="A neighbour without limits uses the node's memory",
    ),
    Scenario(
        "unschedulable-memory",
        "unschedulable",
        [_pod(0, phase="Pending"), _pod(1)],
        [
            _event(
                "e1",
                "FailedScheduling",
                "0/3 nodes are available: 3 Insufficient memory. "
                "preemption: 0/3 nodes are available",
                kind="Pod",
            )
        ],
        description="Requests larger than any node's free memory",
    ),
    Scenario(
        "volume-mount",
        "volume",
        [_pod(0, current="ContainerCreating")],
        [
            _event(
                "e1",
                "FailedMount",
                'MountVolume.SetUp failed for volume "data": '
                "rpc error: code = Internal desc = volume is attached to another node",
            )
        ],
        description="Volume still attached to the old node",
    ),
    Scenario(
        "dns",
        "dns",
        [_pod(0, restarts=2), _pod(1)],
        [
            _event(
                "e1",
                "Failed",
                "dial tcp: lookup orders-db.shop.svc.cluster.local on 10.96.0.10:53: no such host",
            )
        ],
        description="Service name typo / CoreDNS problems",
    ),
    Scenario("healthy", None, [_pod(0), _pod(1), _pod(2)], description="Control: nothing wrong"),
]


def run(scenarios: list[Scenario] | None = None) -> dict[str, Any]:
    """Run every scenario in a throwaway in-memory database and score the diagnoses."""
    from sqlalchemy import event as sa_event

    from costlens.accounts import service as accounts
    from costlens.db import Database
    from costlens.db.tenant import set_tenant
    from costlens.k8s.investigate import gather, investigate
    from costlens.k8s.service import ingest_events, ingest_objects
    from costlens.models import K8sCluster, K8sUsage

    db = Database("sqlite://")
    sa_event.listen(db.engine, "connect", lambda conn, _: conn.execute("pragma foreign_keys=on"))
    Base.metadata.create_all(db.engine)
    results = []
    try:
        with db.session() as s:
            user = accounts.sign_in_with_identity(
                s, provider="lab", subject="lab", email="lab@costlens.local", email_verified=True
            )
            org_id = accounts.create_organization(s, name="Lab", owner=user).id
        for sc in scenarios or SCENARIOS:
            with db.session() as s:
                set_tenant(s, org_id=org_id)
                cluster = K8sCluster(
                    id=uuid.uuid4(),
                    org_id=org_id,
                    name=f"lab-{sc.name}",
                    token_hash="0" * 64,
                    token_hint="lab",  # noqa: S106 - a label, not a secret
                )
                s.add(cluster)
                s.flush()
                ingest_objects(
                    s,
                    cluster,
                    "ReplicaSet",
                    "lab",
                    [
                        {
                            "uid": "rs-app",
                            "name": "app-5d8f",
                            "namespace": "shop",
                            "data": {
                                "owners": [{"kind": "Deployment", "name": "app", "uid": "dep-app"}]
                            },
                        }
                    ],
                    final=True,
                )
                ingest_objects(s, cluster, "Node", "lab", sc.nodes, final=True)
                ingest_objects(s, cluster, "Pod", "lab", sc.pods, final=True)
                ingest_events(s, cluster, [*sc.events, *NOISE])
                if sc.usage:
                    s.add(
                        K8sUsage(
                            org_id=org_id,
                            cluster_id=cluster.id,
                            hour=NOW.replace(minute=0),
                            namespace="shop",
                            kind="Deployment",
                            name="app",
                            container="app",
                            samples=60,
                            cpu_sum=6,
                            cpu_max=sc.usage.get("cpu", 0.1),
                            memory_sum=6,
                            memory_max=sc.usage.get("mem", 0.1),
                            cpu_request=0.25,
                            memory_request=0.25,
                            cpu_limit=sc.usage.get("cpu_limit"),
                            memory_limit=sc.usage.get("mem_limit"),
                            replicas=2,
                        )
                    )
                    s.flush()
                ev = gather(
                    s,
                    cluster,
                    namespace="shop",
                    kind="Deployment",
                    name="app",
                    start=NOW - timedelta(hours=6),
                    end=NOW,
                )
                result = investigate(ev, cluster.name)
                checks = [c["check"] for c in result["causes"]]
                top = checks[0] if checks else None
                results.append(
                    {
                        "scenario": sc.name,
                        "expected": sc.expected,
                        "top": top,
                        "rank": (checks.index(sc.expected) + 1) if sc.expected in checks else None,
                        "confidence": result["causes"][0]["confidence"] if checks else None,
                        "correct": top == sc.expected,
                    }
                )
    finally:
        db.dispose()
    correct = sum(r["correct"] for r in results)
    return {
        "at": utcnow().isoformat(),
        "scenarios": len(results),
        "correct": correct,
        "accuracy": round(correct / len(results), 3) if results else None,
        "results": results,
    }


def record(report: dict[str, Any], path: Path, version: str) -> None:
    """Append one line per run so accuracy can be tracked over time."""
    line = {
        "at": report["at"],
        "version": version,
        "accuracy": report["accuracy"],
        "correct": report["correct"],
        "scenarios": report["scenarios"],
        "misses": [r["scenario"] for r in report["results"] if not r["correct"]],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")


def table(report: dict[str, Any], out: Callable[[str], None] = print) -> None:
    out(f"{'scenario':<24} {'expected':<15} {'top-ranked':<15} result")
    for r in report["results"]:
        out(
            f"{r['scenario']:<24} {r['expected']!s:<15} {r['top']!s:<15} "
            f"{'ok' if r['correct'] else 'MISS (rank ' + str(r['rank']) + ')'}"
        )
    out(f"accuracy: {report['correct']}/{report['scenarios']} = {report['accuracy']:.0%}")
