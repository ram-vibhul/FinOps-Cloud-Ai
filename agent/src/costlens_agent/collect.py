"""Read cluster state through the Kubernetes API (list only, paginated)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from costlens_agent import trim

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


def provider_of(nodes: list[dict[str, Any]]) -> str | None:
    for n in nodes:
        pid = (n.get("data") or {}).get("providerID") or ""
        for prefix, name in (("aws://", "eks"), ("gce://", "gke"), ("azure://", "aks")):
            if pid.startswith(prefix):
                return name
    return "other" if nodes else None
