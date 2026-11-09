"""OpenTelemetry metrics (OTLP/HTTP, JSON encoding) as Kubernetes usage samples.

Works with the Collector's ``kubeletstats`` receiver: ``container.cpu.usage`` (cores,
or the older ``container.cpu.utilization``) and ``container.memory.working_set``
(bytes), with ``k8s.namespace.name`` / ``k8s.pod.name`` / ``k8s.container.name`` on the
resource or the data point. Other metrics are ignored."""

from __future__ import annotations

from typing import Any

CPU = {"container.cpu.usage", "container.cpu.utilization", "k8s.container.cpu.usage"}
MEMORY = {"container.memory.working_set", "k8s.container.memory.working_set"}
GIB = 2**30
KEYS = ("k8s.namespace.name", "k8s.pod.name", "k8s.container.name")


def _attrs(items: list[dict[str, Any]] | None) -> dict[str, Any]:
    out = {}
    for a in items or []:
        value = a.get("value") or {}
        for kind in ("stringValue", "intValue", "doubleValue", "boolValue"):
            if kind in value:
                out[a.get("key")] = value[kind]
                break
    return out


def _number(point: dict[str, Any]) -> float | None:
    for key in ("asDouble", "asInt"):
        if key in point:
            try:
                return float(point[key])  # int64 arrives as a string in OTLP JSON
            except (TypeError, ValueError):
                return None
    return None


def samples(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    """(usage samples, data points ignored)."""
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    latest: dict[tuple[tuple[str, str, str], str], int] = {}
    ignored = 0
    for rm in payload.get("resourceMetrics") or []:
        resource = _attrs((rm.get("resource") or {}).get("attributes"))
        for sm in rm.get("scopeMetrics") or []:
            for metric in sm.get("metrics") or []:
                name = metric.get("name")
                field = "cpu" if name in CPU else "memory" if name in MEMORY else None
                data = metric.get("gauge") or metric.get("sum") or {}
                for point in data.get("dataPoints") or []:
                    attrs = {**resource, **_attrs(point.get("attributes"))}
                    key = tuple(str(attrs.get(k) or "") for k in KEYS)
                    value = _number(point)
                    if field is None or not all(key) or value is None or value < 0:
                        ignored += 1
                        continue
                    when = int(point.get("timeUnixNano") or 0)
                    k3 = (key[0], key[1], key[2])
                    if latest.get((k3, field), -1) > when:
                        continue  # keep the newest point per series
                    latest[(k3, field)] = when
                    s = merged.setdefault(
                        k3,
                        {
                            "namespace": k3[0],
                            "pod": k3[1],
                            "container": k3[2],
                            "cpu": 0.0,
                            "memory": 0.0,
                        },
                    )
                    s[field] = value if field == "cpu" else value / GIB
    return list(merged.values()), ignored
