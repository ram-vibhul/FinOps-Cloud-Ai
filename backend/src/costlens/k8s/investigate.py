"""Rule-based incident investigation for a workload or namespace.

Evidence comes from what the agent reports (pod states, events, nodes, usage) plus
recorded changes. Each check looks for one known failure pattern and, when it finds
it, returns a cause with the evidence it used, fixes to try and how to confirm the fix
worked. Causes are ranked by confidence. Logs are not collected yet (agent tasks, 3.8).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from costlens.core.redact import redact
from costlens.k8s.allocation import owner_of
from costlens.models import ChangeEvent, K8sCluster, K8sEvent, K8sObject, K8sUsage

EXIT_CODES = {
    1: "the application exited with an error",
    2: "the command was misused (bad arguments or shell syntax)",
    126: "the command could not be executed (permissions)",
    127: "the command or binary was not found in the image",
    137: "the container was killed (SIGKILL), usually for memory",
    139: "the process crashed with a segmentation fault",
    143: "the container was stopped (SIGTERM) and did not shut down cleanly",
}


@dataclass
class Cause:
    check: str
    title: str
    confidence: float
    summary: str
    evidence: list[str] = field(default_factory=list)
    fixes: list[str] = field(default_factory=list)
    verify: list[str] = field(default_factory=list)


@dataclass
class Evidence:
    namespace: str | None
    workload: tuple[str, str] | None
    start: datetime
    end: datetime
    pods: list[K8sObject]
    events: list[K8sEvent]
    nodes: dict[str, dict[str, Any]]
    usage: dict[str, dict[str, Any]]  # container -> peaks and limits
    rollouts: list[K8sObject]
    changes: list[ChangeEvent]

    @property
    def target(self) -> str:
        if self.workload:
            return f"{self.workload[0]} {self.workload[1]}"
        return f"namespace {self.namespace}" if self.namespace else "the cluster"

    def statuses(self) -> list[tuple[K8sObject, dict[str, Any]]]:
        return [(p, s) for p in self.pods for s in (p.data or {}).get("container_statuses") or []]

    def warnings(self, *reasons: str) -> list[K8sEvent]:
        return [e for e in self.events if e.type == "Warning" and e.reason in reasons]


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _parse_time(value: Any) -> datetime | None:
    try:
        return _aware(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except ValueError:
        return None


def gather(
    db: Session,
    cluster: K8sCluster,
    *,
    namespace: str | None,
    kind: str | None,
    name: str | None,
    start: datetime,
    end: datetime,
) -> Evidence:
    objects = db.scalars(
        select(K8sObject).where(
            K8sObject.cluster_id == cluster.id,
            K8sObject.deleted_at.is_(None),
            K8sObject.kind.in_(("Pod", "ReplicaSet", "Job", "Node")),
        )
    ).all()
    by_uid = {(o.kind, o.uid): o for o in objects}
    workload = (kind, name) if kind and name else None
    pods = [
        o
        for o in objects
        if o.kind == "Pod"
        and (namespace is None or o.namespace == namespace)
        and (workload is None or owner_of(o, by_uid) == workload)
    ]
    pod_names = {p.name for p in pods}
    q = select(K8sEvent).where(
        K8sEvent.cluster_id == cluster.id,
        K8sEvent.last_at >= start - timedelta(hours=1),
        K8sEvent.first_at <= end,
    )
    if namespace:
        q = q.where(K8sEvent.namespace == namespace)
    events = [
        e
        for e in db.scalars(q.order_by(K8sEvent.last_at))
        if workload is None
        or e.object_name in pod_names
        or (e.object_name or "").startswith(workload[1])
    ]
    node_names = {(p.data or {}).get("nodeName") for p in pods}
    nodes = {o.name: o.data or {} for o in objects if o.kind == "Node" and o.name in node_names}
    usage: dict[str, dict[str, Any]] = {}
    if workload and namespace:
        for u in db.scalars(
            select(K8sUsage).where(
                K8sUsage.cluster_id == cluster.id,
                K8sUsage.namespace == namespace,
                K8sUsage.kind == workload[0],
                K8sUsage.name == workload[1],
                K8sUsage.hour >= start - timedelta(days=7),  # memory peaks matter for a week
            )
        ):
            a = usage.setdefault(u.container, {"cpu_peak": 0.0, "mem_peak": 0.0})
            a["cpu_peak"] = max(a["cpu_peak"], u.cpu_max)
            a["mem_peak"] = max(a["mem_peak"], u.memory_max)
            a.update(
                cpu_limit=u.cpu_limit,
                mem_limit=u.memory_limit,
                cpu_request=u.cpu_request,
                mem_request=u.memory_request,
            )
    rollouts = []
    for o in objects:
        if o.kind != "ReplicaSet" or (namespace and o.namespace != namespace):
            continue
        owners = (o.data or {}).get("owners") or []
        if workload and not (owners and (owners[0].get("kind"), owners[0].get("name")) == workload):
            continue
        created = _parse_time((o.data or {}).get("creationTimestamp"))
        if created and start - timedelta(hours=2) <= created <= end:
            rollouts.append(o)
    terms = [t for t in (workload[1] if workload else None, namespace) if t]
    cq = select(ChangeEvent).where(
        ChangeEvent.occurred_at >= start - timedelta(hours=6), ChangeEvent.occurred_at <= end
    )
    if terms:
        cq = cq.where(
            or_(
                *[
                    col.ilike(f"%{t}%")
                    for t in terms
                    for col in (
                        ChangeEvent.title,
                        ChangeEvent.resource_id,
                        ChangeEvent.service,
                        ChangeEvent.environment,
                    )
                ]
            )
        )
    changes = list(db.scalars(cq.order_by(ChangeEvent.occurred_at.desc()).limit(10)))
    return Evidence(namespace, workload, start, end, pods, events, nodes, usage, rollouts, changes)


# --- checks -----------------------------------------------------------------------


def _restarts(ev: Evidence) -> int:
    return sum(int(s.get("restartCount") or 0) for _, s in ev.statuses())


def check_oom(ev: Evidence) -> Cause | None:
    killed = [
        (p, s) for p, s in ev.statuses() if (s.get("last") or {}).get("reason") == "OOMKilled"
    ]
    oom_events = ev.warnings("OOMKilling", "OOMKilled")
    near = [
        (c, u)
        for c, u in ev.usage.items()
        if u.get("mem_limit") and u["mem_peak"] >= u["mem_limit"] * 0.9
    ]
    if not killed and not oom_events:
        return None
    evidence = (
        [
            f"{len(killed)} container(s) last ended with OOMKilled (exit 137); "
            f"{_restarts(ev)} restarts in total"
        ]
        if killed
        else []
    )
    evidence += [f"Event {e.reason} x{e.count}: {e.message[:160]}" for e in oom_events[:3]]
    for c, u in near:
        evidence.append(
            f"Container {c}: memory peak {u['mem_peak']:.2f} GiB vs limit {u['mem_limit']:.2f} GiB"
        )
    limit = next((u["mem_limit"] for _, u in near), None)
    target = f" to at least {limit * 1.3:.2f} GiB" if limit else ""
    return Cause(
        "oom",
        "Out of memory: the container uses more memory than its limit",
        0.9 if (killed and (oom_events or near)) else 0.75,
        f"{ev.target} is killed by the kernel when it goes over its memory limit, then restarts.",
        evidence,
        [
            f"Raise the memory limit{target} (and the request to match peak use).",
            "If memory keeps growing over time, look for a leak or an unbounded cache/batch size.",
            "For JVM/Node.js/Python workers, cap heap or worker count to fit the limit.",
        ],
        ["No OOMKilled restarts for 24 hours.", "Memory peak stays below 80% of the new limit."],
    )


def check_crash_loop(ev: Evidence) -> Cause | None:
    looping = [
        (p, s)
        for p, s in ev.statuses()
        if (s.get("current") or {}).get("reason") == "CrashLoopBackOff"
        and (s.get("last") or {}).get("reason") != "OOMKilled"
    ]
    if not looping and not ev.warnings("BackOff"):
        return None
    if not looping and any(
        (s.get("last") or {}).get("reason") == "OOMKilled" for _, s in ev.statuses()
    ):
        return None  # back-off caused by OOM: the OOM check explains it
    codes = Counter((s.get("last") or {}).get("exitCode") for _, s in looping)
    evidence = [f"{len(looping)} container(s) in CrashLoopBackOff; {_restarts(ev)} restarts"]
    for code, n in codes.most_common(2):
        if code is not None:
            meaning = EXIT_CODES.get(int(code), "see the logs")
            evidence.append(f"Exit code {code} ({n} x): {meaning}")
    evidence += [f"Event BackOff x{e.count}: {e.message[:160]}" for e in ev.warnings("BackOff")[:2]]
    code = codes.most_common(1)[0][0] if codes else None
    fixes = ["Read the previous container's logs: kubectl logs <pod> -c <container> --previous"]
    if code == 127:
        fixes.append("Check the image's entrypoint/command: the binary is missing from the image.")
    elif code in (1, 2):
        fixes.append("Check recent configuration or environment changes the app reads at start-up.")
    if ev.rollouts or ev.changes:
        fixes.append(
            "If it started with the latest rollout, roll back: kubectl rollout undo <deployment>"
        )
    return Cause(
        "crash_loop",
        "The container keeps crashing on start-up",
        0.8 if looping else 0.55,
        f"{ev.target} starts, fails and is restarted with growing delays.",
        evidence,
        fixes,
        ["Pods stay Running and Ready for 30 minutes.", "Restart count stops increasing."],
    )


def check_image_pull(ev: Evidence) -> Cause | None:
    bad = [
        (p, s)
        for p, s in ev.statuses()
        if (s.get("current") or {}).get("reason")
        in ("ImagePullBackOff", "ErrImagePull", "InvalidImageName")
    ]
    events = [e for e in ev.warnings("Failed", "ErrImagePull") if "pull" in e.message.lower()]
    if not bad and not events:
        return None
    images = {c.get("image") for p, _ in bad for c in (p.data or {}).get("containers") or []}
    msg = " ".join(e.message for e in events).lower()
    hint = (
        "The registry refused the credentials: check imagePullSecrets."
        if any(w in msg for w in ("unauthorized", "denied", "authentication"))
        else "Check the image name and tag exist in the registry."
    )
    return Cause(
        "image_pull",
        "The image cannot be pulled",
        0.9,
        f"{ev.target} cannot start because the node cannot download its image.",
        [f"{len(bad)} container(s) waiting with an image pull error"]
        + [f"Image: {i}" for i in sorted(images - {None})][:3]
        + [f"Event: {e.message[:160]}" for e in events[:2]],
        [hint, "If the tag was just changed in a deployment, correct it or roll back."],
        ["Pods reach Running.", "No ErrImagePull/ImagePullBackOff events for 15 minutes."],
    )


def check_config(ev: Evidence) -> Cause | None:
    bad = [
        (p, s)
        for p, s in ev.statuses()
        if (s.get("current") or {}).get("reason")
        in ("CreateContainerConfigError", "CreateContainerError")
    ]
    events = [
        e
        for e in ev.warnings("Failed")
        if re.search(r"configmap|secret|not found|key", e.message, re.I)
    ]
    if not bad and not events:
        return None
    return Cause(
        "config",
        "Missing configuration (ConfigMap or Secret)",
        0.85,
        f"{ev.target} refers to a ConfigMap, Secret or key that does not exist.",
        [f"{len(bad)} container(s) blocked by a configuration error"]
        + [f"Event: {e.message[:160]}" for e in events[:3]],
        ["Create the missing ConfigMap/Secret (or key) in this namespace, or fix the reference."],
        ["Pods reach Running.", "No CreateContainerConfigError for 15 minutes."],
    )


def check_unschedulable(ev: Evidence) -> Cause | None:
    pending = [
        p
        for p in ev.pods
        if (p.data or {}).get("phase") == "Pending" and not (p.data or {}).get("nodeName")
    ]
    events = ev.warnings("FailedScheduling")
    if not pending and not events:
        return None
    msg = " ".join(e.message for e in events)
    fixes = []
    if re.search(r"insufficient (cpu|memory)", msg, re.I):
        fixes.append(
            "Not enough free CPU/memory on any node: lower requests (see right-sizing) "
            "or add nodes."
        )
    if "taint" in msg:
        fixes.append(
            "Nodes have taints the pod does not tolerate: add a toleration or use other nodes."
        )
    if "affinity" in msg or "selector" in msg:
        fixes.append("No node matches the pod's node selector/affinity: check labels.")
    if "persistentvolumeclaim" in msg.lower():
        fixes.append("A PersistentVolumeClaim is not bound: check the storage class and volume.")
    return Cause(
        "unschedulable",
        "Pods cannot be scheduled on any node",
        0.85 if events else 0.6,
        f"{len(pending)} pod(s) of {ev.target} are waiting for a node.",
        [f"{len(pending)} pod(s) Pending without a node"]
        + [f"Event FailedScheduling: {e.message[:200]}" for e in events[:2]],
        fixes or ["Read the FailedScheduling event for the reason."],
        ["All pods are scheduled and Running.", "No FailedScheduling events for 15 minutes."],
    )


def check_probes(ev: Evidence) -> Cause | None:
    events = [e for e in ev.warnings("Unhealthy") if "probe" in e.message.lower()]
    if not events:
        return None
    liveness = any("liveness" in e.message.lower() for e in events)
    return Cause(
        "probes",
        f"{'Liveness' if liveness else 'Readiness'} probe failures",
        0.7 if liveness else 0.55,
        "Kubernetes "
        + (
            "restarts the container because its liveness probe fails."
            if liveness
            else "stops sending traffic because the readiness probe fails."
        ),
        [f"Event Unhealthy x{e.count}: {e.message[:160]}" for e in events[:3]],
        [
            "Check the probe path/port and that the app answers within timeoutSeconds.",
            "Give slow starters a startupProbe or a longer initialDelaySeconds.",
            "If the app is overloaded, probes time out: check CPU throttling and dependencies.",
        ],
        ["No Unhealthy events for 30 minutes.", "Pods stay Ready."],
    )


def check_node_pressure(ev: Evidence) -> Cause | None:
    bad = []
    for name, data in ev.nodes.items():
        for c in data.get("conditions") or []:
            if (
                c.get("type") in ("MemoryPressure", "DiskPressure", "PIDPressure")
                and c.get("status") == "True"
            ) or (c.get("type") == "Ready" and c.get("status") != "True"):
                bad.append(f"Node {name}: {c.get('type')}={c.get('status')}")
    evicted = ev.warnings("Evicted", "NodeNotReady", "EvictionThresholdMet")
    if not bad and not evicted:
        return None
    return Cause(
        "node",
        "Node problems (pressure or not ready)",
        0.75,
        f"Pods of {ev.target} run on nodes that are short of resources or unhealthy.",
        bad[:4] + [f"Event {e.reason}: {e.message[:160]}" for e in evicted[:2]],
        [
            "Cordon and drain the affected node, or replace it.",
            "Check for pods without limits using the node's memory or disk.",
        ],
        ["Nodes report Ready with no pressure conditions.", "No evictions for 1 hour."],
    )


def check_mounts(ev: Evidence) -> Cause | None:
    events = ev.warnings("FailedMount", "FailedAttachVolume")
    if not events:
        return None
    return Cause(
        "volume",
        "A volume cannot be attached or mounted",
        0.8,
        f"{ev.target} waits for storage that is not available.",
        [f"Event {e.reason} x{e.count}: {e.message[:160]}" for e in events[:3]],
        [
            "Check the PersistentVolumeClaim is Bound and in the node's zone.",
            "A volume still attached to an old node can block: wait or detach it.",
        ],
        ["Pods reach Running.", "No FailedMount events for 15 minutes."],
    )


def check_throttling(ev: Evidence) -> Cause | None:
    hot = [
        (c, u)
        for c, u in ev.usage.items()
        if u.get("cpu_limit") and u["cpu_peak"] >= u["cpu_limit"] * 0.9
    ]
    if not hot:
        return None
    return Cause(
        "cpu_throttling",
        "CPU limit reached (throttling)",
        0.5,
        f"{ev.target} hits its CPU limit, which slows it down (timeouts, failed probes).",
        [
            f"Container {c}: CPU peak {u['cpu_peak']:.2f} cores vs limit {u['cpu_limit']:.2f}"
            for c, u in hot
        ],
        ["Raise or remove the CPU limit (keep the request), or scale out."],
        ["CPU peak stays below 80% of the limit.", "Latency/probe failures stop."],
    )


def check_dns(ev: Evidence) -> Cause | None:
    events = [e for e in ev.events if re.search(r"no such host|dns|lookup .* on", e.message, re.I)]
    if not events:
        return None
    return Cause(
        "dns",
        "Name resolution (DNS) failures",
        0.45,
        f"{ev.target} cannot resolve some host names.",
        [f"Event {e.reason}: {e.message[:160]}" for e in events[:3]],
        [
            "Check CoreDNS pods and their logs in kube-system.",
            "Check the service name/namespace is spelled right.",
        ],
        ["No DNS errors for 30 minutes."],
    )


# Stable names for each check (used when closing an investigation with the real cause).
CHECK_LABELS = {
    "oom": "Out of memory",
    "crash_loop": "Crash on start-up (application error)",
    "image_pull": "Image cannot be pulled",
    "config": "Missing ConfigMap or Secret",
    "unschedulable": "Cannot be scheduled",
    "probes": "Liveness / readiness probe",
    "node": "Node problem",
    "volume": "Volume attach / mount",
    "cpu_throttling": "CPU limit (throttling)",
    "dns": "DNS / name resolution",
}

CHECKS: list[Callable[[Evidence], Cause | None]] = [
    check_oom,
    check_crash_loop,
    check_image_pull,
    check_config,
    check_unschedulable,
    check_probes,
    check_node_pressure,
    check_mounts,
    check_throttling,
    check_dns,
]


def _first(items: list[str], default: str = "") -> str:
    return items[0] if items else default


def narrative(target: str, facts: dict[str, Any], causes: list[Cause], hours: float) -> str:
    """Plain-English summary from the findings (used as is, or reworded by AI)."""
    seen = (
        f"In the last {hours:g} hours, {facts['running']} of {facts['pods']} pod(s) of {target} "
        f"were running, with {facts['restarts']} restart(s) and {facts['warning_events']} "
        "warning event(s)."
    )
    if not causes:
        return seen + " None of the known failure patterns match, so nothing looks wrong."
    top = causes[0]
    text = (
        f"{seen} The most likely cause is: {top.title.lower()} "
        f"({round(top.confidence * 100)}% confidence). {top.summary} "
        f"The strongest evidence: {_first(top.evidence).rstrip('.')}. "
        f"Try first: {_first(top.fixes)} It worked when: {_first(top.verify).lower()}"
    )
    if len(causes) > 1:
        text += " Also worth checking: " + ", ".join(c.title.lower() for c in causes[1:3]) + "."
    return text


def status_update(
    target: str, where: str, facts: dict[str, Any], causes: list[Cause], hours: float
) -> str:
    """A short update ready to paste into Slack, Teams or an email."""
    seen = (
        f"{facts['running']}/{facts['pods']} pods running, {facts['restarts']} restarts, "
        f"{facts['warning_events']} warnings in the last {hours:g} h"
    )
    if not causes:
        return (
            f"[All clear] {target} ({where})\nWhat we see: {seen}.\nNo known failure pattern found."
        )
    top = causes[0]
    lines = [
        f"[Investigating] {target} ({where})",
        f"What we see: {seen}.",
        f"Likely cause: {top.title} ({round(top.confidence * 100)}% confidence).",
        f"Next step: {_first(top.fixes)}",
        f"Done when: {_first(top.verify)}",
    ]
    if facts.get("changes"):
        lines.insert(2, f"Recent change: {facts['changes'][0]}")
    return "\n".join(lines)


def investigate(ev: Evidence, cluster_name: str | None = None) -> dict[str, Any]:
    causes = [c for c in (check(ev) for check in CHECKS) if c]
    change_notes = [
        f"Rollout: new ReplicaSet {r.name} created {(r.data or {}).get('creationTimestamp')}"
        for r in ev.rollouts
    ] + [f"Change: {c.title} ({c.source}, {_aware(c.occurred_at):%d %b %H:%M})" for c in ev.changes]
    if change_notes:
        for c in causes:  # failures that began right after a change: more likely related
            c.confidence = min(0.97, c.confidence + 0.05)
            c.evidence.append("Started after: " + change_notes[0])
    for c in causes:  # event messages can carry secrets or personal data
        c.evidence = [redact(e) for e in c.evidence]
    causes.sort(key=lambda c: c.confidence, reverse=True)
    running = sum(1 for p in ev.pods if (p.data or {}).get("phase") == "Running")
    facts = {
        "pods": len(ev.pods),
        "running": running,
        "restarts": _restarts(ev),
        "warning_events": sum(1 for e in ev.events if e.type == "Warning"),
        "nodes": sorted(ev.nodes),
        "changes": [redact(n) for n in change_notes[:5]],
        "logs": "Fetch logs on demand below (read by the agent, redacted in the cluster).",
        "pod_list": [
            {
                "name": p.name,
                "namespace": p.namespace,
                "containers": [c.get("name") for c in (p.data or {}).get("containers") or []],
                "restarts": sum(
                    int(s.get("restartCount") or 0)
                    for s in (p.data or {}).get("container_statuses") or []
                ),
            }
            for p in ev.pods[:10]
        ],
    }
    hours = round((ev.end - ev.start).total_seconds() / 3600, 1)
    where = " / ".join(x for x in (cluster_name, ev.namespace) if x) or "cluster"
    summary = (
        f"Most likely: {causes[0].title.lower()} ({round(causes[0].confidence * 100)}% confidence)."
        if causes
        else f"No known failure pattern found for {ev.target}: "
        f"{running} of {len(ev.pods)} pod(s) running."
    )
    return {
        "target": ev.target,
        "summary": summary,
        "narrative": narrative(ev.target, facts, causes, hours),
        "status_update": status_update(ev.target, where, facts, causes, hours),
        "worded_by": "template",
        "causes": [asdict(c) | {"confidence": round(c.confidence, 2)} for c in causes],
        "facts": facts,
        "timeline": [
            {
                "at": _aware(e.last_at).isoformat(),
                "type": e.type,
                "reason": e.reason,
                "object": f"{e.object_kind}/{e.object_name}",
                "message": redact(e.message[:300]),
                "count": e.count,
            }
            for e in ev.events[-50:]
        ]
        + [
            {
                "at": _aware(c.occurred_at).isoformat(),
                "type": "Change",
                "reason": c.kind,
                "object": c.source,
                "message": redact(c.title),
                "count": 1,
            }
            for c in ev.changes
        ],
    }


AI_SUMMARY = (
    "You explain Kubernetes incidents to engineers and their managers. Rewrite the findings "
    "in plain English in three to five sentences: what is happening, the most likely cause "
    "and what to do first. Use only the facts given; never invent causes, numbers, names "
    "or commands. No headings, no lists, no markdown."
)
AI_STATUS = (
    "Write a team status update about a Kubernetes incident for Slack, Teams or email. "
    "At most five short lines: status in brackets with the workload, what we see, likely "
    "cause, next step, and when it counts as fixed. Use only the facts given; never invent "
    "anything. Plain text, no markdown."
)


def reword(writer: Any, result: dict[str, Any]) -> None:
    """AI wording from the (already redacted) template text. Any failure keeps the template."""
    facts = redact(
        result["narrative"]
        + "\n\nCauses:\n"
        + "\n".join(
            f"- {c['title']} ({round(c['confidence'] * 100)}%): {'; '.join(c['evidence'][:3])}. "
            f"Fix: {c['fixes'][0] if c['fixes'] else ''}"
            for c in result["causes"][:3]
        )
    )
    summary = writer.rewrite(facts, system=AI_SUMMARY)
    status = writer.rewrite(
        facts + "\n\nTemplate update:\n" + result["status_update"], system=AI_STATUS
    )
    if summary:
        result.update(template_narrative=result["narrative"], narrative=summary, worded_by="ai")
    if status:
        result.update(
            template_status_update=result["status_update"], status_update=status, worded_by="ai"
        )
