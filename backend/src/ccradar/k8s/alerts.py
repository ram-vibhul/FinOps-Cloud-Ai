"""Start investigations from incoming alerts (Prometheus Alertmanager, PagerDuty,
Opsgenie). Deliveries are authenticated, alerts are de-duplicated by fingerprint, and
each alert is mapped to a workload from its labels or details."""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ccradar.core.notify import Message, notify
from ccradar.core.redact import redact
from ccradar.db.base import utcnow
from ccradar.k8s.allocation import owner_of
from ccradar.k8s.correlate import attach
from ccradar.k8s.investigate import gather, investigate
from ccradar.models import InboundWebhook, K8sCluster, K8sInvestigation, K8sObject

KINDS = ("alertmanager", "pagerduty", "opsgenie")
WORKLOAD_LABELS = (
    ("deployment", "Deployment"),
    ("statefulset", "StatefulSet"),
    ("daemonset", "DaemonSet"),
    ("cronjob", "CronJob"),
    ("job_name", "Job"),
    ("job", "Job"),
)
_TEXT_NS = re.compile(r"\b(?:namespace|ns)[=: ]+([a-z0-9]([-a-z0-9]*[a-z0-9])?)", re.I)
_TEXT_WL = re.compile(
    r"\b(deployment|statefulset|daemonset|cronjob|job|pod)[=: /]+([a-z0-9][-a-z0-9.]*[a-z0-9])",
    re.I,
)


@dataclass
class Alert:
    fingerprint: str
    firing: bool
    title: str
    labels: dict[str, str] = field(default_factory=dict)
    started_at: datetime | None = None


def verify(kind: str, secret: str, body: bytes, headers: dict[str, str]) -> bool:
    """PagerDuty signs (v1 HMAC-SHA256); Alertmanager and Opsgenie send the secret as a
    Bearer token (they cannot sign)."""
    if kind == "pagerduty":
        expected = "v1=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        sent = [s.strip() for s in headers.get("x-pagerduty-signature", "").split(",")]
        return any(hmac.compare_digest(s, expected) for s in sent if s)
    auth = headers.get("authorization", "")
    token = (
        auth[7:]
        if auth.lower().startswith("bearer ")
        else headers.get("x-cloudcostradar-token", "")
    )
    return bool(token) and hmac.compare_digest(token, secret)


def _from_text(text: str) -> dict[str, str]:
    labels: dict[str, str] = {}
    if m := _TEXT_NS.search(text):
        labels["namespace"] = m.group(1).lower()
    if m := _TEXT_WL.search(text):
        key = {"job": "job_name"}.get(m.group(1).lower(), m.group(1).lower())
        labels[key] = m.group(2).lower()
    return labels


def _when(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None
    except ValueError:
        return None


def parse(kind: str, payload: dict[str, Any]) -> list[Alert]:
    if kind == "alertmanager":
        out = []
        for a in payload.get("alerts") or []:
            labels = {str(k): str(v) for k, v in (a.get("labels") or {}).items()}
            ann = a.get("annotations") or {}
            title = ann.get("summary") or labels.get("alertname") or "Alert"
            fp = (
                a.get("fingerprint")
                or hashlib.sha256(repr(sorted(labels.items())).encode()).hexdigest()[:16]
            )
            out.append(
                Alert(
                    f"alertmanager:{fp}",
                    a.get("status") != "resolved",
                    title,
                    labels,
                    _when(a.get("startsAt")),
                )
            )
        return out
    if kind == "pagerduty":
        event = payload.get("event") or {}
        data = event.get("data") or {}
        etype = event.get("event_type") or ""
        if not etype.startswith("incident.") or not data.get("id"):
            return []
        title = data.get("title") or "PagerDuty incident"
        service = (data.get("service") or {}).get("summary") or ""
        return [
            Alert(
                f"pagerduty:{data['id']}",
                etype not in ("incident.resolved",),
                title,
                _from_text(f"{title} {service}"),
                _when(event.get("occurred_at")),
            )
        ]
    if kind == "opsgenie":
        alert = payload.get("alert") or {}
        if not alert.get("alertId"):
            return []
        details = {str(k): str(v) for k, v in (alert.get("details") or {}).items()}
        tags = dict(t.split(":", 1) for t in alert.get("tags") or [] if ":" in t)
        title = alert.get("message") or "Opsgenie alert"
        labels = {**_from_text(title), **tags, **details}
        action = (payload.get("action") or "").lower()
        return [
            Alert(
                f"opsgenie:{alert['alertId']}",
                action not in ("close", "delete"),
                title,
                labels,
                _when(alert.get("createdAt")),
            )
        ]
    return []


def target(
    db: Session, cluster: K8sCluster, labels: dict[str, str]
) -> tuple[str | None, str | None, str | None]:
    """(namespace, kind, name) for an alert; pods resolve to their workload."""
    ns = (
        labels.get("namespace")
        or labels.get("kubernetes_namespace")
        or labels.get("k8s.namespace.name")
    )
    for label, kind in WORKLOAD_LABELS:
        if labels.get(label):
            return ns, kind, labels[label]
    pod_name = labels.get("pod") or labels.get("pod_name") or labels.get("k8s.pod.name")
    if pod_name and ns:
        objects = db.scalars(
            select(K8sObject).where(
                K8sObject.cluster_id == cluster.id,
                K8sObject.deleted_at.is_(None),
                K8sObject.kind.in_(("Pod", "ReplicaSet", "Job")),
            )
        ).all()
        pod = next(
            (o for o in objects if o.kind == "Pod" and o.name == pod_name and o.namespace == ns),
            None,
        )
        if pod is not None:
            kind, name = owner_of(pod, {(o.kind, o.uid): o for o in objects})
            return ns, kind, name
        return ns, "Pod", pod_name
    return ns, None, None


def handle(
    db: Session, hook: InboundWebhook, cluster: K8sCluster, alerts: list[Alert], base_url: str
) -> dict[str, int]:
    now = utcnow()
    counts = {"opened": 0, "refreshed": 0, "resolved": 0}
    messages: list[Message] = []
    link = base_url.rstrip("/") + "/kubernetes/investigations/{}"
    for a in alerts:
        existing = db.scalar(
            select(K8sInvestigation).where(
                K8sInvestigation.cluster_id == cluster.id,
                K8sInvestigation.fingerprint == a.fingerprint,
                K8sInvestigation.status == "open",
            )
        )
        if not a.firing:
            if existing is not None:
                existing.status = "resolved"
                existing.result = {**(existing.result or {}), "resolved_at": now.isoformat()}
                counts["resolved"] += 1
            continue
        ns, kind, name = target(db, cluster, a.labels)
        start = min(a.started_at or now, now) - timedelta(hours=2)
        ev = gather(db, cluster, namespace=ns, kind=kind, name=name, start=start, end=now)
        result = investigate(ev, cluster.name)
        attach(db, cluster, result, ns, kind, name, start, now)
        result["alert"] = {
            "source": hook.kind,
            "receiver": hook.name,
            "title": redact(a.title),
            "fingerprint": a.fingerprint,
            "labels": {k: redact(v) for k, v in list(a.labels.items())[:30]},
        }
        title = f"[{hook.kind}] {redact(a.title)}: {result['summary']}"[:300]
        if existing is not None:
            existing.result, existing.title = result, title
            existing.window_start, existing.window_end = start, now
            counts["refreshed"] += 1
            continue
        inv = K8sInvestigation(
            org_id=cluster.org_id,
            cluster_id=cluster.id,
            namespace=ns,
            kind=kind,
            name=name,
            window_start=start,
            window_end=now,
            trigger="alert",
            status="open",
            title=title,
            result=result,
            fingerprint=a.fingerprint,
        )
        db.add(inv)
        db.flush()
        counts["opened"] += 1
        messages.append(
            Message(
                title=f"Alert investigated: {redact(a.title)}",
                text=result["status_update"],
                link=link.format(inv.id),
            )
        )
    hook.last_received_at = now
    db.flush()
    if messages:
        notify(db, "k8s_incident", messages)
    return counts
