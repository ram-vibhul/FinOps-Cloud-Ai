"""Container usage from Prometheus (instead of metrics-server when configured).

Only two fixed, read-only instant queries are sent; customers' own queries come with
3.18 (validated and rate-limited)."""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

CPU = (
    "sum by (namespace, pod, container) "
    '(rate(container_cpu_usage_seconds_total{container!="",container!="POD"}[5m]))'
)
MEMORY = (
    "sum by (namespace, pod, container) "
    '(container_memory_working_set_bytes{container!="",container!="POD"})'
)
GIB = 2**30
log = logging.getLogger(__name__)


class Prometheus:
    def __init__(
        self, url: str, *, token_file: str | None = None, http: httpx.Client | None = None
    ) -> None:
        self.url = url.rstrip("/")
        headers = {}
        if token_file:
            headers["Authorization"] = f"Bearer {Path(token_file).read_text().strip()}"
        self.http = http or httpx.Client(timeout=20.0)
        self.headers = headers

    def query(self, promql: str) -> list[dict[str, Any]]:
        response = self.http.get(
            f"{self.url}/api/v1/query", params={"query": promql}, headers=self.headers
        )
        response.raise_for_status()
        body = response.json()
        if body.get("status") != "success":
            raise RuntimeError(f"Prometheus query failed: {body.get('error', 'unknown error')}")
        return (body.get("data") or {}).get("result") or []

    def instant(self, promql: str, limit: int = 50) -> list[tuple[dict[str, str], float]]:
        """An instant query as (labels, value) pairs, largest first, at most ``limit``."""
        response = self.http.get(
            f"{self.url}/api/v1/query",
            params={"query": promql, "timeout": "10s"},
            headers=self.headers,
            timeout=15.0,
        )
        response.raise_for_status()
        body = response.json()
        if body.get("status") != "success":
            raise RuntimeError(f"Prometheus query failed: {body.get('error', 'unknown error')}")
        data = body.get("data") or {}
        if data.get("resultType") == "scalar":
            pairs = [({}, float((data.get("result") or [0, "nan"])[1]))]
        elif data.get("resultType") == "vector":
            pairs = [
                (
                    {str(k): str(v) for k, v in (r.get("metric") or {}).items()},
                    float((r.get("value") or [0, "nan"])[1]),
                )
                for r in data.get("result") or []
            ]
        else:
            raise RuntimeError("only instant vector or scalar results are supported")
        pairs = [(labels, v) for labels, v in pairs if math.isfinite(v)]
        return sorted(pairs, key=lambda p: -p[1])[:limit]

    def usage_samples(self, watched: Callable[[str | None], bool]) -> list[dict[str, Any]]:
        """Per-container CPU (cores) and memory (GiB), same shape as metrics-server."""
        merged: dict[tuple[str, str, str], dict[str, Any]] = {}
        for promql, field, scale in ((CPU, "cpu", 1.0), (MEMORY, "memory", 1 / GIB)):
            for series in self.query(promql):
                m = series.get("metric") or {}
                ns, pod, container = m.get("namespace"), m.get("pod"), m.get("container")
                if not (ns and pod and container) or not watched(ns):
                    continue
                key = (str(ns), str(pod), str(container))
                try:
                    value = float((series.get("value") or [0, "0"])[1]) * scale
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(value):  # NaN or +Inf from empty rates
                    continue
                sample = merged.setdefault(
                    key,
                    {
                        "namespace": key[0],
                        "pod": key[1],
                        "container": key[2],
                        "cpu": 0.0,
                        "memory": 0.0,
                    },
                )
                sample[field] = round(value, 6)
        return list(merged.values())


def from_env(env: dict[str, str]) -> Prometheus | None:
    url = env.get("COSTLENS_PROMETHEUS_URL", "").strip()
    if not url:
        return None
    return Prometheus(url, token_file=env.get("COSTLENS_PROMETHEUS_TOKEN_FILE") or None)


# --- auto-discovery -------------------------------------------------------------------

KNOWN_NAMES = (
    "prometheus-operated",
    "kube-prometheus-stack-prometheus",
    "prometheus-k8s",
    "prometheus-server",
    "prometheus",
)
NOT_PROMETHEUS = ("alertmanager", "pushgateway", "exporter", "adapter", "operator", "blackbox")


def candidates(services: list[dict[str, Any]]) -> list[str]:
    """In-cluster URLs of services that look like a Prometheus server, best first."""
    scored: list[tuple[int, str]] = []
    for svc in services:
        name, ns = svc.get("name") or "", svc.get("namespace") or "default"
        data = svc.get("data") or {}
        labels = data.get("labels") or {}
        if any(word in name for word in NOT_PROMETHEUS):
            continue
        by_label = (
            labels.get("app.kubernetes.io/name") == "prometheus"
            or labels.get("app") == "prometheus"
        )
        if name not in KNOWN_NAMES and not by_label:
            continue
        ports = [p.get("port") for p in data.get("ports") or [] if p.get("port")]
        port = 9090 if 9090 in ports else (ports[0] if ports else 9090)
        rank = KNOWN_NAMES.index(name) if name in KNOWN_NAMES else len(KNOWN_NAMES)
        scored.append((rank, f"http://{name}.{ns}.svc:{port}"))
    return [url for _, url in sorted(scored)]


def discover(services: list[dict[str, Any]], probe: Callable[[str], bool]) -> str | None:
    for url in candidates(services):
        try:
            if probe(url):
                return url
        except Exception as error:  # unreachable candidate: try the next one
            log.debug("Prometheus candidate %s not usable: %s", url, type(error).__name__)
    return None


def probe(url: str, http: httpx.Client | None = None) -> bool:
    client = http or httpx.Client(timeout=5.0)
    response = client.get(f"{url}/api/v1/status/buildinfo")
    return response.status_code == 200 and response.json().get("status") == "success"
