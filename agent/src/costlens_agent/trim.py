"""Turn full Kubernetes objects (as plain dicts) into the small, safe subset CostLens
needs. Anything not listed here is dropped, so new fields never leak by default."""

from __future__ import annotations

import os
from typing import Any

from costlens_agent.redact import redact

Obj = dict[str, Any]
REDACT_IPS = os.environ.get("COSTLENS_REDACT_IPS") == "true"


def _pick(src: Obj | None, *keys: str) -> Obj:
    if not src:
        return {}
    return {k: src[k] for k in keys if src.get(k) is not None}


def _meta(obj: Obj) -> Obj:
    m = obj.get("metadata") or {}
    out = _pick(m, "labels", "creationTimestamp", "generation")
    owners = [_pick(o, "kind", "name", "uid") for o in m.get("ownerReferences") or []]
    if owners:
        out["owners"] = owners
    return out


def _conditions(status: Obj | None) -> list[Obj]:
    return [
        _pick(c, "type", "status", "reason", "lastTransitionTime")
        for c in (status or {}).get("conditions") or []
    ]


def _containers(spec: Obj | None, key: str = "containers") -> list[Obj]:
    # Only name, image and resources: env, command, args and mounts are never sent.
    return [
        {**_pick(c, "name", "image"), "resources": _pick(c.get("resources"), "requests", "limits")}
        for c in (spec or {}).get(key) or []
    ]


def _state(state: Obj | None) -> Obj:
    for name, detail in (state or {}).items():
        if detail:
            return {"state": name, **_pick(detail, "reason", "exitCode", "startedAt", "finishedAt")}
    return {}


def _template(spec: Obj | None) -> Obj:
    pod_spec = ((spec or {}).get("template") or {}).get("spec") or {}
    return {
        "containers": _containers(pod_spec),
        "init_containers": _containers(pod_spec, "initContainers"),
    }


def pod(obj: Obj) -> Obj:
    spec, status = obj.get("spec") or {}, obj.get("status") or {}
    return {
        **_meta(obj),
        **_pick(spec, "nodeName", "priorityClassName"),
        "containers": _containers(spec),
        "init_containers": _containers(spec, "initContainers"),
        **_pick(status, "phase", "qosClass", "startTime", "reason"),
        # Pod IPs map VPC Flow Log traffic to workloads (network cost attribution);
        # COSTLENS_REDACT_IPS=true keeps them in the cluster.
        **({} if REDACT_IPS else _pick(status, "podIP")),
        **_pick(spec, "hostNetwork"),
        "conditions": _conditions(status),
        "container_statuses": [
            {
                **_pick(c, "name", "ready", "restartCount"),
                "current": _state(c.get("state")),
                "last": _state(c.get("lastState")),
            }
            for c in status.get("containerStatuses") or []
        ],
    }


def node(obj: Obj) -> Obj:
    spec, status = obj.get("spec") or {}, obj.get("status") or {}
    return {
        **_meta(obj),
        **_pick(spec, "providerID", "unschedulable"),
        "taints": [_pick(t, "key", "effect") for t in spec.get("taints") or []],
        **_pick(status, "capacity", "allocatable"),
        "conditions": _conditions(status),
        "info": _pick(
            status.get("nodeInfo"),
            "kubeletVersion",
            "osImage",
            "architecture",
            "containerRuntimeVersion",
        ),
    }


def workload(obj: Obj) -> Obj:
    spec, status = obj.get("spec") or {}, obj.get("status") or {}
    return {
        **_meta(obj),
        **_pick(spec, "replicas"),
        "selector": (spec.get("selector") or {}).get("matchLabels") or {},
        **_template(spec),
        **_pick(
            status,
            "replicas",
            "readyReplicas",
            "availableReplicas",
            "updatedReplicas",
            "observedGeneration",
            "currentNumberScheduled",
            "desiredNumberScheduled",
            "numberReady",
        ),
        "conditions": _conditions(status),
    }


def job(obj: Obj) -> Obj:
    spec, status = obj.get("spec") or {}, obj.get("status") or {}
    return {
        **_meta(obj),
        **_pick(spec, "completions", "parallelism", "backoffLimit"),
        **_template(spec),
        **_pick(status, "active", "succeeded", "failed", "startTime", "completionTime"),
        "conditions": _conditions(status),
    }


def cronjob(obj: Obj) -> Obj:
    spec = obj.get("spec") or {}
    return {
        **_meta(obj),
        **_pick(spec, "schedule", "suspend"),
        **_pick(obj.get("status"), "lastScheduleTime", "lastSuccessfulTime"),
    }


def service(obj: Obj) -> Obj:
    spec = obj.get("spec") or {}
    return {
        **_meta(obj),
        **_pick(spec, "type", "selector"),
        "ports": [_pick(p, "port", "protocol", "targetPort") for p in spec.get("ports") or []],
    }


def pvc(obj: Obj) -> Obj:
    spec, status = obj.get("spec") or {}, obj.get("status") or {}
    return {
        **_meta(obj),
        **_pick(spec, "storageClassName", "volumeName"),
        "requested": ((spec.get("resources") or {}).get("requests") or {}).get("storage"),
        **_pick(status, "phase", "capacity"),
    }


def hpa(obj: Obj) -> Obj:
    spec, status = obj.get("spec") or {}, obj.get("status") or {}
    return {
        **_meta(obj),
        "target": _pick(spec.get("scaleTargetRef"), "kind", "name"),
        **_pick(spec, "minReplicas", "maxReplicas"),
        "metrics": [m.get("type") for m in spec.get("metrics") or []],
        **_pick(status, "currentReplicas", "desiredReplicas"),
    }


def namespace(obj: Obj) -> Obj:
    return {**_meta(obj), **_pick(obj.get("status"), "phase")}


TRIMMERS = {
    "Namespace": namespace,
    "Node": node,
    "Pod": pod,
    "Deployment": workload,
    "ReplicaSet": workload,
    "StatefulSet": workload,
    "DaemonSet": workload,
    "Job": job,
    "CronJob": cronjob,
    "Service": service,
    "PersistentVolumeClaim": pvc,
    "HorizontalPodAutoscaler": hpa,
}


def to_record(kind: str, obj: Obj) -> Obj:
    m = obj.get("metadata") or {}
    return {
        "uid": m["uid"],
        "name": m["name"],
        "namespace": m.get("namespace"),
        "data": TRIMMERS[kind](obj),
    }


def event(obj: Obj) -> Obj | None:
    m = obj.get("metadata") or {}
    involved = obj.get("involvedObject") or obj.get("regarding") or {}
    last = obj.get("lastTimestamp") or obj.get("eventTime") or m.get("creationTimestamp")
    first = obj.get("firstTimestamp") or last
    if not m.get("uid") or not last:
        return None
    return {
        "uid": m["uid"],
        "namespace": m.get("namespace"),
        "object_kind": involved.get("kind"),
        "object_name": involved.get("name"),
        "type": "Warning" if obj.get("type") == "Warning" else "Normal",
        "reason": (obj.get("reason") or "")[:128],
        # Secrets and personal data never leave the cluster (IPs too when configured).
        "message": redact((obj.get("message") or obj.get("note") or "")[:4000], ips=REDACT_IPS),
        "count": int(obj.get("count") or 1),
        "first_at": first,
        "last_at": last,
    }
