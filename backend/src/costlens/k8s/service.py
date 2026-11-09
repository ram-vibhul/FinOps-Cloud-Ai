"""Cluster tokens and ingest of what the agent reports."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from costlens.core.redact import redact
from costlens.db.base import utcnow
from costlens.db.tenant import set_tenant
from costlens.models import K8sCluster, K8sEvent, K8sMetricQuery, K8sObject

PREFIX = "ckt"
KINDS = frozenset(
    {
        "Namespace",
        "Node",
        "Pod",
        "Deployment",
        "ReplicaSet",
        "StatefulSet",
        "DaemonSet",
        "Job",
        "CronJob",
        "Service",
        "PersistentVolumeClaim",
        "HorizontalPodAutoscaler",
    }
)
SYSTEM_NAMESPACES = ("kube-system", "kube-public", "kube-node-lease")
LATEST_AGENT = "0.1.0"


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def issue_token(cluster: K8sCluster) -> str:
    """New token for ``cluster`` (replaces any previous one). Shown once."""
    secret = secrets.token_urlsafe(32)
    cluster.token_hash, cluster.token_hint = _digest(secret), secret[-6:]
    return f"{PREFIX}_{cluster.id.hex}_{secret}"


def authenticate(db: Session, token: str | None) -> K8sCluster | None:
    """The cluster a bearer token belongs to (constant-time compare), or None."""
    if not token:
        return None
    parts = token.split("_", 2)
    if len(parts) != 3 or parts[0] != PREFIX:
        return None
    try:
        cluster_id = uuid.UUID(hex=parts[1])
    except ValueError:
        return None
    set_tenant(db, bypass=True)
    cluster = db.get(K8sCluster, cluster_id)
    if cluster is None or not hmac.compare_digest(cluster.token_hash, _digest(parts[2])):
        return None
    set_tenant(db, org_id=cluster.org_id)
    return cluster


def agent_config(cluster: K8sCluster, db: Session | None = None) -> dict[str, Any]:
    settings = cluster.settings or {}
    return {
        "cluster_id": str(cluster.id),
        "include_namespaces": settings.get("include_namespaces", []),
        "exclude_namespaces": settings.get("exclude_namespaces", list(SYSTEM_NAMESPACES)),
        "snapshot_interval_seconds": settings.get("snapshot_interval_seconds", 300),
        "events_interval_seconds": settings.get("events_interval_seconds", 60),
        "usage_interval_seconds": settings.get("usage_interval_seconds", 60),
        "tasks_interval_seconds": settings.get("tasks_interval_seconds", 10),
        "metric_interval_seconds": settings.get("metric_interval_seconds", 300),
        "latest_agent_version": LATEST_AGENT,
        "metric_queries": []
        if db is None
        else [
            {"id": str(q.id), "promql": q.promql}
            for q in db.scalars(
                select(K8sMetricQuery)
                .where(K8sMetricQuery.cluster_id == cluster.id, K8sMetricQuery.enabled.is_(True))
                .order_by(K8sMetricQuery.created_at)
                .limit(10)
            )
        ],
    }


def ingest_objects(
    db: Session,
    cluster: K8sCluster,
    kind: str,
    snapshot_id: str,
    objects: Iterable[dict[str, Any]],
    *,
    final: bool,
) -> dict[str, int]:
    """Upsert one chunk of a kind's snapshot; on the final chunk, objects of that kind
    not seen in this snapshot are marked deleted."""
    now = utcnow()
    items = {o["uid"]: o for o in objects}
    existing = (
        {
            row.uid: row
            for row in db.scalars(
                select(K8sObject).where(
                    K8sObject.cluster_id == cluster.id,
                    K8sObject.kind == kind,
                    K8sObject.uid.in_(list(items)),
                )
            )
        }
        if items
        else {}
    )
    created = 0
    for uid, o in items.items():
        row = existing.get(uid)
        if row is None:
            row = K8sObject(
                org_id=cluster.org_id,
                cluster_id=cluster.id,
                kind=kind,
                uid=uid,
                first_seen_at=now,
            )
            db.add(row)
            created += 1
        row.namespace, row.name, row.data = o.get("namespace"), o["name"], o.get("data", {})
        row.snapshot_id, row.last_seen_at, row.deleted_at = snapshot_id, now, None
    db.flush()
    deleted = 0
    if final:
        result = db.execute(
            update(K8sObject)
            .where(
                K8sObject.cluster_id == cluster.id,
                K8sObject.kind == kind,
                K8sObject.snapshot_id != snapshot_id,
                K8sObject.deleted_at.is_(None),
            )
            .values(deleted_at=now)
        )
        deleted = result.rowcount or 0  # type: ignore[attr-defined]
    _seen(cluster, now)
    if kind == "Node" and final:
        cluster.node_count = len(
            db.scalars(
                select(K8sObject.id).where(
                    K8sObject.cluster_id == cluster.id,
                    K8sObject.kind == "Node",
                    K8sObject.deleted_at.is_(None),
                )
            ).all()
        )
    db.flush()
    return {"received": len(items), "created": created, "deleted": deleted}


def ingest_events(db: Session, cluster: K8sCluster, events: Iterable[dict[str, Any]]) -> int:
    items = {e["uid"]: e for e in events}
    existing = (
        {
            row.uid: row
            for row in db.scalars(
                select(K8sEvent).where(
                    K8sEvent.cluster_id == cluster.id, K8sEvent.uid.in_(list(items))
                )
            )
        }
        if items
        else {}
    )
    for uid, e in items.items():
        row = existing.get(uid)
        if row is None:
            row = K8sEvent(org_id=cluster.org_id, cluster_id=cluster.id, uid=uid)
            db.add(row)
        row.namespace, row.object_kind, row.object_name = (
            e.get("namespace"),
            e.get("object_kind"),
            e.get("object_name"),
        )
        row.type, row.reason = e.get("type") or "Normal", (e.get("reason") or "")[:128]
        # Agents redact already; redact again here for older agents or custom senders.
        row.message = redact(e.get("message") or "")[:2000]
        row.count = int(e.get("count") or 1)
        row.first_at, row.last_at = e["first_at"], e["last_at"]
    _seen(cluster, utcnow())
    db.flush()
    return len(items)


def _seen(cluster: K8sCluster, now: datetime) -> None:
    cluster.last_seen_at, cluster.status = now, "connected"
