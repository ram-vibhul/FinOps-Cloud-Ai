"""A tiny Prometheus text-format metrics registry (no extra dependency).

Metrics are per process; with several Uvicorn workers, scrape each worker or
run one worker per container (the Kubernetes/compose default).
"""

from __future__ import annotations

import math
import threading
from collections import defaultdict
from collections.abc import Sequence

LabelValues = tuple[str, ...]


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _labels(names: Sequence[str], values: Sequence[str], extra: str = "") -> str:
    parts = [f'{n}="{_escape(v)}"' for n, v in zip(names, values, strict=True)]
    if extra:
        parts.append(extra)
    return "{" + ",".join(parts) + "}" if parts else ""


def _fmt(value: float) -> str:
    if math.isinf(value):
        return "+Inf"
    return repr(float(value)) if not float(value).is_integer() else str(int(value))


class _Metric:
    kind = ""

    def __init__(self, name: str, help_text: str, labels: Sequence[str] = ()) -> None:
        self.name = name
        self.help = help_text
        self.label_names = tuple(labels)
        self._lock = threading.Lock()

    def _key(self, labels: dict[str, str]) -> LabelValues:
        if set(labels) != set(self.label_names):
            raise ValueError(
                f"{self.name}: expected labels {self.label_names}, got {tuple(labels)}"
            )
        return tuple(str(labels[n]) for n in self.label_names)

    def header(self) -> list[str]:
        return [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} {self.kind}"]

    def samples(self) -> list[str]:
        raise NotImplementedError


class Counter(_Metric):
    kind = "counter"

    def __init__(self, name: str, help_text: str, labels: Sequence[str] = ()) -> None:
        super().__init__(name, help_text, labels)
        self._values: dict[LabelValues, float] = defaultdict(float)

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        if amount < 0:
            raise ValueError("counters only go up")
        key = self._key(labels)
        with self._lock:
            self._values[key] += amount

    def value(self, **labels: str) -> float:
        return self._values.get(self._key(labels), 0.0)

    def samples(self) -> list[str]:
        with self._lock:
            items = sorted(self._values.items())
        return [f"{self.name}{_labels(self.label_names, k)} {_fmt(v)}" for k, v in items]


class Gauge(Counter):
    kind = "gauge"

    def set(self, value: float, **labels: str) -> None:
        key = self._key(labels)
        with self._lock:
            self._values[key] = value


DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


class Histogram(_Metric):
    kind = "histogram"

    def __init__(
        self,
        name: str,
        help_text: str,
        labels: Sequence[str] = (),
        buckets: Sequence[float] = DEFAULT_BUCKETS,
    ) -> None:
        super().__init__(name, help_text, labels)
        self.buckets = (*sorted(buckets), math.inf)
        self._counts: dict[LabelValues, list[int]] = {}
        self._sums: dict[LabelValues, float] = defaultdict(float)

    def observe(self, value: float, **labels: str) -> None:
        key = self._key(labels)
        with self._lock:
            counts = self._counts.setdefault(key, [0] * len(self.buckets))
            for i, bound in enumerate(self.buckets):
                if value <= bound:
                    counts[i] += 1
            self._sums[key] += value

    def count(self, **labels: str) -> int:
        counts = self._counts.get(self._key(labels))
        return counts[-1] if counts else 0

    def samples(self) -> list[str]:
        out: list[str] = []
        with self._lock:
            items = sorted((k, list(v)) for k, v in self._counts.items())
            sums = dict(self._sums)
        for key, counts in items:
            for bound, count in zip(self.buckets, counts, strict=True):
                le = f'le="{_fmt(bound)}"'
                out.append(f"{self.name}_bucket{_labels(self.label_names, key, le)} {count}")
            out.append(f"{self.name}_sum{_labels(self.label_names, key)} {_fmt(sums[key])}")
            out.append(f"{self.name}_count{_labels(self.label_names, key)} {counts[-1]}")
        return out


class Registry:
    def __init__(self) -> None:
        self._metrics: dict[str, _Metric] = {}

    def register[M: _Metric](self, metric: M) -> M:
        if metric.name in self._metrics:
            raise ValueError(f"metric {metric.name} already registered")
        self._metrics[metric.name] = metric
        return metric

    def render(self) -> str:
        lines: list[str] = []
        for metric in self._metrics.values():
            lines += metric.header() + metric.samples()
        return "\n".join(lines) + "\n"


REGISTRY = Registry()
HTTP_REQUESTS = REGISTRY.register(
    Counter("costlens_http_requests_total", "HTTP requests.", ("method", "route", "status"))
)
HTTP_DURATION = REGISTRY.register(
    Histogram(
        "costlens_http_request_duration_seconds", "HTTP request duration.", ("method", "route")
    )
)
JOBS = REGISTRY.register(Counter("costlens_jobs_total", "Background jobs run.", ("job", "outcome")))
BUILD_INFO = REGISTRY.register(
    Gauge("costlens_build_info", "Build and deployment information.", ("version", "mode"))
)

PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
