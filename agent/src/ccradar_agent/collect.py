"""Read cluster state through the Kubernetes API (list only, paginated)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from ccradar_agent import quantity, trim

PAGE = 500
Lister = Callable[..., Any]


def default_listers(kube: Any) -> dict[str, Lister]:
    """List calls for every kind the agent reads (``kube`` is a ``kube.Kube``)."""
    from ccradar_agent.kube import LIST_PATHS

    return {kind: kube.lister(kind) for kind in LIST_PATHS}


class Collector:
    def __init__(self, listers: dict[str, Lister], serialize: Callable[[Any], Any]) -> None:
        self.listers = listers
        self.serialize = serialize
        self.include: set[str] = set()
        self.exclude: set[str] = set()

    def configure(self, include: list[str], exclude: list[str]) -> None:
        self.include, self.exclude = set(include), set(exclude)

    def watched(self, namespace: str | None) -> bool:
        if namespace is None:  # cluster-scoped (nodes, namespaces)
            return True
        if self.include:
            return namespace in self.include
        return namespace not in self.exclude

    def raw(self, kind: str) -> Iterator[dict[str, Any]]:
        """Every object of a kind, unfiltered (discovery needs all namespaces)."""
        return self._raw(kind)

    def _raw(self, kind: str) -> Iterator[dict[str, Any]]:
        cont: str | None = None
        while True:
            kwargs: dict[str, Any] = {"limit": PAGE}
            if cont:
                kwargs["_continue"] = cont
            items, cont = _items(self.listers[kind](**kwargs))
            for item in items:
                yield self.serialize(item)
            if not cont:
                return

    def objects(self, kind: str) -> Iterator[dict[str, Any]]:
        for obj in self._raw(kind):
            ns = (obj.get("metadata") or {}).get("namespace")
            if self.watched(ns):
                yield trim.to_record(kind, obj)

    def events(self) -> Iterator[dict[str, Any]]:
        for obj in self._raw("Event"):
            record = trim.event(obj)
            if record and self.watched(record["namespace"]):
                yield record


def _items(page: Any) -> tuple[list[Any], str | None]:
    if isinstance(page, dict):  # custom objects API returns plain dicts
        return page.get("items") or [], (page.get("metadata") or {}).get("continue")
    cont = getattr(page.metadata, "_continue", None) if page.metadata else None
    return list(page.items or []), cont


def usage_samples(collector: Collector) -> list[dict[str, Any]]:
    """Current per-container CPU (cores) and memory (GiB) from metrics-server."""
    out = []
    cont: str | None = None
    while True:
        kwargs: dict[str, Any] = {"limit": PAGE}
        if cont:
            kwargs["_continue"] = cont
        items, cont = _items(collector.listers["PodMetrics"](**kwargs))
        for item in items:
            m = item.get("metadata") or {}
            if not collector.watched(m.get("namespace")):
                continue
            for c in item.get("containers") or []:
                usage = c.get("usage") or {}
                out.append(
                    {
                        "namespace": m.get("namespace"),
                        "pod": m.get("name"),
                        "container": c.get("name"),
                        "cpu": round(quantity.parse(usage.get("cpu")), 6),
                        "memory": round(quantity.parse(usage.get("memory")) / quantity.GIB, 6),
                    }
                )
        if not cont:
            return out


def provider_of(nodes: list[dict[str, Any]]) -> str | None:
    for n in nodes:
        pid = (n.get("data") or {}).get("providerID") or ""
        for prefix, name in (("aws://", "eks"), ("gce://", "gke"), ("azure://", "aks")):
            if pid.startswith(prefix):
                return name
    return "other" if nodes else None
