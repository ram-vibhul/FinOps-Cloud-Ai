"""A small read-only Kubernetes API client (11.19).

The agent needs only a handful of GET calls: list (paginated), read one object, read a
pod's logs and the server version. Doing them with httpx keeps the official client
(about 127 MB with its generated models) out of the image. Answers are the API's own
JSON, which is what the official client's ``sanitize_for_serialization`` produced.

In-cluster only: the API address comes from the service environment variables, the
service account token is re-read from its projected file (Kubernetes rotates it), and
the cluster CA verifies the server. Only GET is ever sent.
"""

from __future__ import annotations

import os
import ssl
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")
TIMEOUT = 30.0

# kind -> collection path for "list across all namespaces" (or cluster-scoped)
LIST_PATHS = {
    "Namespace": "/api/v1/namespaces",
    "Node": "/api/v1/nodes",
    "Pod": "/api/v1/pods",
    "Service": "/api/v1/services",
    "PersistentVolumeClaim": "/api/v1/persistentvolumeclaims",
    "Deployment": "/apis/apps/v1/deployments",
    "ReplicaSet": "/apis/apps/v1/replicasets",
    "StatefulSet": "/apis/apps/v1/statefulsets",
    "DaemonSet": "/apis/apps/v1/daemonsets",
    "Job": "/apis/batch/v1/jobs",
    "CronJob": "/apis/batch/v1/cronjobs",
    "HorizontalPodAutoscaler": "/apis/autoscaling/v2/horizontalpodautoscalers",
    "Event": "/api/v1/events",
    "PodMetrics": "/apis/metrics.k8s.io/v1beta1/pods",
}
# kind -> namespaced path prefix for reading one object
READ_PATHS = {
    "Pod": "/api/v1/namespaces/{ns}/pods/{name}",
    "Deployment": "/apis/apps/v1/namespaces/{ns}/deployments/{name}",
    "StatefulSet": "/apis/apps/v1/namespaces/{ns}/statefulsets/{name}",
    "DaemonSet": "/apis/apps/v1/namespaces/{ns}/daemonsets/{name}",
    "Job": "/apis/batch/v1/namespaces/{ns}/jobs/{name}",
    "CronJob": "/apis/batch/v1/namespaces/{ns}/cronjobs/{name}",
}


class KubeError(RuntimeError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class Kube:
    def __init__(
        self,
        base_url: str,
        token: Callable[[], str],
        http: httpx.Client,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.http = http

    @classmethod
    def in_cluster(cls, sa_dir: Path = SA_DIR) -> Kube:
        host = os.environ.get("KUBERNETES_SERVICE_HOST")
        port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
        if not host:
            raise KubeError("not running inside a Kubernetes cluster (no KUBERNETES_SERVICE_HOST)")
        if ":" in host:  # IPv6
            host = f"[{host}]"
        context = ssl.create_default_context(cafile=str(sa_dir / "ca.crt"))
        token_file = sa_dir / "token"
        return cls(
            f"https://{host}:{port}",
            lambda: token_file.read_text().strip(),  # re-read: projected tokens rotate
            httpx.Client(verify=context, timeout=TIMEOUT, follow_redirects=False),
        )

    def get(self, path: str, params: dict[str, Any] | None = None, *, text: bool = False) -> Any:
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        try:
            r = self.http.get(
                f"{self.base_url}{path}",
                params=clean,
                headers={"Authorization": f"Bearer {self.token()}", "Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise KubeError(f"Kubernetes API unreachable ({type(exc).__name__})") from exc
        if r.status_code >= 400:
            raise KubeError(f"Kubernetes API answered {r.status_code} for {path}", r.status_code)
        return r.text if text else r.json()

    # --- what the collector and tasks use -------------------------------------------

    def lister(self, kind: str) -> Callable[..., dict[str, Any]]:
        path = LIST_PATHS[kind]

        def list_page(limit: int | None = None, _continue: str | None = None, **_: Any) -> dict:
            return self.get(path, {"limit": limit, "continue": _continue})

        return list_page

    def reader(self, kind: str) -> Callable[[str, str], dict[str, Any]]:
        template = READ_PATHS[kind]
        return lambda name, namespace: self.get(template.format(ns=namespace, name=name))

    def read_log(
        self,
        name: str,
        namespace: str,
        *,
        tail_lines: int | None = None,
        previous: bool = False,
        timestamps: bool = False,
        container: str | None = None,
        since_seconds: int | None = None,
    ) -> str:
        params = {
            "tailLines": tail_lines,
            "previous": "true" if previous else None,
            "timestamps": "true" if timestamps else None,
            "container": container,
            "sinceSeconds": since_seconds,
        }
        return self.get(f"/api/v1/namespaces/{namespace}/pods/{name}/log", params, text=True)

    def events(self, namespace: str, field_selector: str | None = None) -> dict[str, Any]:
        return self.get(f"/api/v1/namespaces/{namespace}/events", {"fieldSelector": field_selector})

    def version(self) -> str | None:
        return (self.get("/version") or {}).get("gitVersion")


def identity(obj: Any) -> Any:
    """The API's JSON is already serialised."""
    return obj
