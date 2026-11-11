"""Kubernetes detectors."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from ccradar.findings.registry import FindingIn, detector
from ccradar.k8s.rightsizing import recommendations

MIN_SAVING = 1.0
KUBECTL_KIND = {
    "Deployment": "deployment",
    "StatefulSet": "statefulset",
    "DaemonSet": "daemonset",
    "CronJob": "cronjob",
    "Job": "job",
}


def _cpu(v: float) -> str:
    return f"{round(v * 1000)}m"


def _mem(v: float) -> str:
    return f"{round(v * 1024)}Mi"


def _finding(r: dict, currency: str) -> FindingIn:
    workload = f"{r['cluster_id']}/{r['namespace']}/{r['kind']}/{r['workload']}"
    req_cpu, req_mem = _cpu(r["cpu_request"]), _mem(r["memory_request_gib"])
    peak_cpu, peak_mem = _cpu(r["cpu_peak"]), _mem(r["memory_peak_gib"])
    new_cpu, new_mem = _cpu(r["recommended_cpu"]), _mem(r["recommended_memory_gib"])
    kind = KUBECTL_KIND.get(r["kind"])
    target = f"{r['workload']}/{r['container']}"
    commands = []
    if kind:
        commands.append(
            f"kubectl -n {r['namespace']} set resources {kind}/{r['workload']} "
            f"-c {r['container']} --requests=cpu={new_cpu},memory={new_mem}"
        )
    return FindingIn(
        key=f"{workload}/{r['container']}",
        title=f"Lower requests for {target} in {r['cluster']}/{r['namespace']}",
        description=(
            f"Requests are {req_cpu} CPU and {req_mem} memory per replica, but the highest "
            f"use in {r['hours']} hours was {peak_cpu} and {peak_mem}. The suggestion keeps "
            f"{r['headroom_pct']}% headroom above that peak."
            + (
                f" It runs {r['replicas']} replica(s) but its reliability target asks for at "
                f"least {r['min_replicas']}: add replicas before lowering requests."
                if r["replicas_below_target"]
                else ""
            )
        ),
        savings_monthly=r["monthly_saving"],
        currency=currency,
        risk="low",
        effort="low",
        overlap=workload,
        provider="kubernetes",
        service="Kubernetes",
        resource_type=r["kind"],
        resource_id=workload,
        steps=[
            f"Set requests to {new_cpu} CPU and {new_mem} memory in the workload's manifest "
            "(Helm values or GitOps repository).",
            "Deploy, then watch restarts and latency for a day (Kubernetes → Investigate).",
        ],
        commands=commands,
        evidence={
            k: r[k]
            for k in (
                "cpu_request",
                "cpu_peak",
                "memory_request_gib",
                "memory_peak_gib",
                "recommended_cpu",
                "recommended_memory_gib",
                "hours",
                "replicas",
            )
        },
    )


@detector("k8s.rightsizing", category="kubernetes", title="Over-sized container requests")
def rightsizing(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    return [
        _finding(r, currency)
        for r in recommendations(db, org_id, currency)["items"]
        if r["action"] == "decrease" and r["monthly_saving"] >= MIN_SAVING
    ]
