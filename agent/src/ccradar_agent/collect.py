"""Read cluster state through the Kubernetes API (list only, paginated)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from ccradar_agent import quantity, trim

PAGE = 500
Lister = Callable[..., Any]


def default_listers(api_client: Any) -> dict[str, Lister]:
    from kubernetes import client

    core = client.CoreV1Api(api_client)
    apps = client.AppsV1Api(api_client)
    batch = client.BatchV1Api(api_client)
    autoscaling = client.AutoscalingV2Api(api_client)
    return {
        "Namespace": core.list_namespace,
        "Node": core.list_node,
        "Pod": core.list_pod_for_all_namespaces,
        "Service": core.list_service_for_all_namespaces,
        "PersistentVolumeClaim": core.list_persistent_volume_claim_for_all_namespaces,
        "Deployment": apps.list_deployment_for_all_namespaces,
        "ReplicaSet": apps.list_replica_set_for_all_namespaces,
        "StatefulSet": apps.list_stateful_set_for_all_namespaces,
        "DaemonSet": apps.list_daemon_set_for_all_namespaces,
        "Job": batch.list_job_for_all_namespaces,
        "CronJob": batch.list_cron_job_for_all_namespaces,
        "HorizontalPodAutoscaler": autoscaling.list_horizontal_pod_autoscaler_for_all_namespaces,
        "Event": core.list_event_for_all_namespaces,
        "PodMetrics": lambda **kw: client.CustomObjectsApi(api_client).list_cluster_custom_object(
            "metrics.k8s.io", "v1beta1", "pods", **kw
        ),
    }


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
            page = self.listers[kind](**kwargs)
            for item in page.items or []:
                yield self.serialize(item)
            cont = getattr(page.metadata, "_continue", None) if page.metadata else None
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
