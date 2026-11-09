"""Workload health from what the agent reports, and automatic incidents: a workload that
starts failing gets an investigation (trigger "auto") and a notification; when it
recovers, the investigation is resolved and a "resolved" notification goes out."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from costlens.core.notify import Message, notify
from costlens.k8s.allocation import owner_of
from costlens.k8s.correlate import attach
from costlens.k8s.investigate import gather, investigate
from costlens.models import K8sCluster, K8sInvestigation, K8sObject

FAILING = {
    "CrashLoopBackOff",
    "ImagePullBackOff",
    "ErrImagePull",
    "CreateContainerConfigError",
    "CreateContainerError",
    "InvalidImageName",
}
INCIDENT_EVENT = "k8s_incident"


def workload_health(db: Session, cluster_id: Any) -> list[dict[str, Any]]:
    objects = db.scalars(
        select(K8sObject).where(
            K8sObject.cluster_id == cluster_id,
            K8sObject.deleted_at.is_(None),
            K8sObject.kind.in_(("Pod", "ReplicaSet", "Job")),
        )
    ).all()
    by_uid = {(o.kind, o.uid): o for o in objects}
    groups: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(
        lambda: {"pods": 0, "running": 0, "restarts": 0, "reasons": set(), "pending": 0}
    )
    for pod in (o for o in objects if o.kind == "Pod"):
        kind, name = owner_of(pod, by_uid)
        g = groups[(pod.namespace or "", kind, name)]
        d = pod.data or {}
        g["pods"] += 1
        g["running"] += d.get("phase") == "Running"
        g["pending"] += d.get("phase") == "Pending"
        for s in d.get("container_statuses") or []:
            g["restarts"] += int(s.get("restartCount") or 0)
            for state in ("current", "last"):
                reason = (s.get(state) or {}).get("reason")
                if reason and (state == "current" or reason == "OOMKilled"):
                    g["reasons"].add(reason)
    out = []
    for (ns, kind, name), g in groups.items():
        failing = bool(g["reasons"] & FAILING)
        warning = g["pending"] > 0 or g["restarts"] >= 5 or "OOMKilled" in g["reasons"]
        out.append(
            {
                "namespace": ns,
                "kind": kind,
                "name": name,
                "pods": g["pods"],
                "running": g["running"],
                "restarts": g["restarts"],
                "reasons": sorted(g["reasons"]),
                "health": "failing" if failing else "warning" if warning else "ok",
            }
        )
    rank = {"failing": 0, "warning": 1, "ok": 2}
    out.sort(key=lambda w: (rank[w["health"]], w["namespace"], w["name"]))
    return out


def _incident(w: dict[str, Any]) -> bool:
    return w["health"] == "failing" or "OOMKilled" in w["reasons"]


def detect(db: Session, cluster: K8sCluster, now: datetime, base_url: str) -> dict[str, int]:
    """Open an auto investigation for each newly failing workload; resolve recovered ones."""
    health = workload_health(db, cluster.id)
    open_auto = {
        (i.namespace, i.kind, i.name): i
        for i in db.scalars(
            select(K8sInvestigation).where(
                K8sInvestigation.cluster_id == cluster.id,
                K8sInvestigation.trigger == "auto",
                K8sInvestigation.status == "open",
            )
        )
    }
    current = {(w["namespace"], w["kind"], w["name"]): w for w in health}
    # Workloads already under an open alert investigation are not opened twice.
    alerted = {
        (i.namespace, i.kind, i.name)
        for i in db.scalars(
            select(K8sInvestigation).where(
                K8sInvestigation.cluster_id == cluster.id,
                K8sInvestigation.trigger == "alert",
                K8sInvestigation.status == "open",
            )
        )
    }
    messages: list[Message] = []
    opened = resolved = 0
    link = base_url.rstrip("/") + "/kubernetes/investigations/{}"
    for key, w in current.items():
        if not _incident(w) or key in open_auto or key in alerted:
            continue
        ns, kind, name = key
        ev = gather(
            db, cluster, namespace=ns, kind=kind, name=name, start=now - timedelta(hours=6), end=now
        )
        result = investigate(ev, cluster.name)
        attach(db, cluster, result, ns, kind, name, now - timedelta(hours=6), now)
        inv = K8sInvestigation(
            org_id=cluster.org_id,
            cluster_id=cluster.id,
            namespace=ns,
            kind=kind,
            name=name,
            window_start=now - timedelta(hours=6),
            window_end=now,
            trigger="auto",
            status="open",
            title=f"{result['target']}: {result['summary']}"[:300],
            result=result,
        )
        db.add(inv)
        db.flush()
        opened += 1
        messages.append(
            Message(
                title=f"{kind} {name} is failing in {cluster.name}/{ns}",
                text=result["status_update"],
                link=link.format(inv.id),
                severity="alert",
            )
        )
    for key, inv in open_auto.items():
        w = current.get(key)
        if w is not None and _incident(w):
            continue
        inv.status = "resolved"
        inv.result = {**(inv.result or {}), "resolved_at": now.isoformat()}
        resolved += 1
        messages.append(
            Message(
                title=f"Resolved: {inv.kind} {inv.name} in {cluster.name}/{inv.namespace}",
                text=f"[Resolved] {inv.kind} {inv.name} is healthy again"
                + ("" if w else " (no longer running)")
                + ".",
                link=link.format(inv.id),
                severity="resolved",
            )
        )
    db.flush()
    sent = notify(db, INCIDENT_EVENT, messages) if messages else 0
    return {"opened": opened, "resolved": resolved, "sent": sent}


def first_scan(db: Session, cluster: K8sCluster, now: datetime) -> dict[str, Any] | None:
    """Once per cluster, right after its first full report: a health summary with the
    likely cause for up to five unhealthy workloads (the first useful answer)."""
    settings = dict(cluster.settings or {})
    if "first_scan" in settings:
        return None
    health = workload_health(db, cluster.id)
    if not health:  # no pods reported yet
        return None
    nodes = db.scalars(
        select(K8sObject.id).where(
            K8sObject.cluster_id == cluster.id,
            K8sObject.kind == "Node",
            K8sObject.deleted_at.is_(None),
        )
    ).all()
    findings = []
    for w in [w for w in health if w["health"] != "ok"][:5]:
        ev = gather(
            db,
            cluster,
            namespace=w["namespace"],
            kind=w["kind"],
            name=w["name"],
            start=now - timedelta(hours=6),
            end=now,
        )
        result = investigate(ev, cluster.name)
        top = result["causes"][0] if result["causes"] else None
        findings.append(
            {
                "namespace": w["namespace"],
                "kind": w["kind"],
                "name": w["name"],
                "health": w["health"],
                "summary": result["summary"],
                "cause": top["title"] if top else None,
                "first_fix": top["fixes"][0] if top and top["fixes"] else None,
            }
        )
    scan = {
        "at": now.isoformat(),
        "workloads": len(health),
        "pods": sum(w["pods"] for w in health),
        "nodes": len(nodes),
        "failing": sum(1 for w in health if w["health"] == "failing"),
        "warnings": sum(1 for w in health if w["health"] == "warning"),
        "findings": findings,
    }
    settings["first_scan"] = scan
    cluster.settings = settings
    db.flush()
    return scan


SILENT_AFTER = timedelta(minutes=15)


def check_agents(db: Session, now: datetime, base_url: str) -> dict[str, int]:
    """Warn once when a cluster's agent stops reporting, and once when it is back."""
    link = base_url.rstrip("/") + "/settings?tab=kubernetes"
    counts = {"silent": 0, "back": 0}
    messages: list[Message] = []
    for c in db.scalars(
        select(K8sCluster).where(
            K8sCluster.last_seen_at.is_not(None), K8sCluster.status != "revoked"
        )
    ):
        last = c.last_seen_at
        if last is None:
            continue
        seen = last if last.tzinfo else last.replace(tzinfo=now.tzinfo)
        silent = now - seen > SILENT_AFTER
        if silent and c.silent_since is None:
            c.silent_since = now
            counts["silent"] += 1
            messages.append(
                Message(
                    title=f"CostLens agent in {c.name} stopped reporting",
                    text=(
                        f"No report since {seen:%d %b %H:%M} UTC. Costs, health checks and "
                        f"alerts for {c.name} are paused until it is back. Check the "
                        "costlens-agent pod (kubectl -n costlens get pods) and its token."
                    ),
                    link=link,
                    severity="alert",
                )
            )
        elif not silent and c.silent_since is not None:
            c.silent_since = None
            counts["back"] += 1
            messages.append(
                Message(
                    title=f"CostLens agent in {c.name} is reporting again",
                    text=f"Reports resumed at {seen:%d %b %H:%M} UTC.",
                    link=link,
                    severity="resolved",
                )
            )
    db.flush()
    if messages:
        notify(db, INCIDENT_EVENT, messages)
    return counts
