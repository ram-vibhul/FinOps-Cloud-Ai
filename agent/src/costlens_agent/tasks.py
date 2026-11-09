"""Read-only tasks requested from CostLens: fetch a pod's logs or describe an object.

The agent re-checks everything itself (the server is not trusted blindly): only these
two kinds, strict names, at most 500 log lines and 24 hours back, never exec or writes.
Output is redacted before it leaves the cluster."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from costlens_agent import trim
from costlens_agent.redact import redact

NAME = re.compile(r"^[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?$")
DESCRIBABLE = ("Pod", "Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob")
ALLOW_LOGS = os.environ.get("COSTLENS_ALLOW_LOGS", "true") != "false"
REDACT_IPS = os.environ.get("COSTLENS_REDACT_IPS") == "true"


class TaskRefused(ValueError):
    pass


@dataclass
class TaskApi:
    """Kubernetes calls the tasks may use (read-only)."""

    read_log: Callable[..., str]
    read: dict[str, Callable[..., Any]]  # kind -> read_namespaced_<kind>(name, namespace)
    events: Callable[..., Any]  # list_namespaced_event(namespace, field_selector=...)
    serialize: Callable[[Any], Any]


def default_task_api(api_client: Any) -> TaskApi:
    from kubernetes import client

    core, apps, batch = (
        client.CoreV1Api(api_client),
        client.AppsV1Api(api_client),
        client.BatchV1Api(api_client),
    )
    return TaskApi(
        read_log=core.read_namespaced_pod_log,
        read={
            "Pod": core.read_namespaced_pod,
            "Deployment": apps.read_namespaced_deployment,
            "StatefulSet": apps.read_namespaced_stateful_set,
            "DaemonSet": apps.read_namespaced_daemon_set,
            "Job": batch.read_namespaced_job,
            "CronJob": batch.read_namespaced_cron_job,
        },
        events=core.list_namespaced_event,
        serialize=api_client.sanitize_for_serialization,
    )


def _name(value: Any, field: str) -> str:
    if not isinstance(value, str) or not NAME.match(value):
        raise TaskRefused(f"invalid {field}")
    return value


def run(
    task: dict[str, Any], api: TaskApi, watched: Callable[[str], bool], prometheus: Any = None
) -> str:
    kind, p = task.get("kind"), task.get("params") or {}
    if kind == "promql":
        return _promql(p.get("promql") or "", prometheus)
    namespace = _name(p.get("namespace"), "namespace")
    if not watched(namespace):
        raise TaskRefused(f"namespace {namespace} is not watched")
    if kind == "logs":
        if not ALLOW_LOGS:
            raise TaskRefused("log access is turned off for this cluster (costlens.allowLogs)")
        kwargs: dict[str, Any] = {
            "tail_lines": min(int(p.get("tail_lines") or 200), 500),
            "previous": bool(p.get("previous")),
            "timestamps": True,
        }
        if p.get("container"):
            kwargs["container"] = _name(p["container"], "container")
        if p.get("since_seconds"):
            kwargs["since_seconds"] = min(int(p["since_seconds"]), 86_400)
        text = api.read_log(_name(p.get("pod"), "pod"), namespace, **kwargs)
        return redact(text or "(no log lines)", ips=REDACT_IPS)
    if kind == "describe":
        obj_kind = p.get("kind")
        if obj_kind not in DESCRIBABLE:
            raise TaskRefused("this kind cannot be described")
        name = _name(p.get("name"), "name")
        obj = api.serialize(api.read[obj_kind](name, namespace))
        record = trim.to_record(obj_kind, obj)  # same safe subset as snapshots: no env
        events = api.serialize(api.events(namespace, field_selector=f"involvedObject.name={name}"))
        lines = [
            f"{obj_kind} {namespace}/{name}",
            json.dumps(record["data"], indent=2, sort_keys=True),
            "",
            "Events:",
        ]
        for e in (events or {}).get("items") or []:
            ev = trim.event(e)
            if ev:
                lines.append(
                    f"  {ev['last_at']}  {ev['type']:<7} {ev['reason']:<20} "
                    f"x{ev['count']}  {ev['message']}"
                )
        if lines[-1] == "Events:":
            lines.append("  (none)")
        return redact("\n".join(lines), ips=REDACT_IPS)
    raise TaskRefused("task kind not allowed")


def _promql(promql: str, prometheus: Any) -> str:
    from costlens_agent.promql import problem

    reason = problem(promql)
    if reason:
        raise TaskRefused(f"query not allowed: {reason}")
    if prometheus is None:
        raise TaskRefused("no Prometheus found in this cluster (set prometheus.url in the chart)")
    pairs = prometheus.instant(promql, limit=20)
    if not pairs:
        return "(no series)"
    lines = [
        f"{v:.6g}  " + ", ".join(f"{k}={val}" for k, val in sorted(labels.items()))
        for labels, v in pairs
    ]
    return redact("\n".join(lines), ips=REDACT_IPS)
