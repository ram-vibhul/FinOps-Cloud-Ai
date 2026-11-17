"""The agent's own read-only Kubernetes client (11.19), against a fake API server."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from ccradar_agent.collect import Collector, default_listers, usage_samples
from ccradar_agent.kube import LIST_PATHS, Kube, KubeError, identity
from ccradar_agent.tasks import default_task_api, run


class FakeApiServer:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.tokens = iter(["t1", "t2", "t3", "t4", "t5", "t6"])

    def __call__(self, request: httpx.Request) -> httpx.Response:  # noqa: PLR0911
        self.requests.append(request)
        path, q = request.url.path, request.url.params
        if path == "/api/v1/pods":
            if q.get("continue") == "page2":
                return httpx.Response(200, json={"items": [pod("b")], "metadata": {}})
            return httpx.Response(
                200, json={"items": [pod("a")], "metadata": {"continue": "page2"}}
            )
        if path == "/apis/metrics.k8s.io/v1beta1/pods":
            return httpx.Response(200, json={"items": [{
                "metadata": {"namespace": "shop", "name": "a"},
                "containers": [{"name": "web", "usage": {"cpu": "250m", "memory": "512Mi"}}],
            }], "metadata": {}})  # fmt: skip
        if path == "/api/v1/namespaces/shop/pods/a/log":
            return httpx.Response(200, text="2026-11-17T10:00:00Z started\n")
        if path == "/api/v1/namespaces/shop/pods/a":
            return httpx.Response(200, json=pod("a"))
        if path == "/api/v1/namespaces/shop/events":
            return httpx.Response(200, json={"items": []})
        if path == "/version":
            return httpx.Response(200, json={"gitVersion": "v1.34.1"})
        return httpx.Response(403, json={"reason": "Forbidden"})

    def kube(self) -> Kube:
        return Kube(
            "https://10.0.0.1:443",
            lambda: next(self.tokens),
            httpx.Client(transport=httpx.MockTransport(self)),
        )


def pod(name: str) -> dict[str, Any]:
    return {
        "kind": "Pod",
        "metadata": {"name": name, "namespace": "shop", "uid": f"uid-{name}",
                     "creationTimestamp": "2026-11-01T00:00:00Z"},
        "spec": {"containers": [{"name": "web", "image": "shop:1",
                                 "env": [{"name": "PASSWORD", "value": "hunter2"}]}]},
        "status": {"phase": "Running"},
    }  # fmt: skip


def test_lists_every_page_with_a_fresh_token() -> None:
    api = FakeApiServer()
    collector = Collector(default_listers(api.kube()), identity)
    names = [o["metadata"]["name"] for o in collector.raw("Pod")]
    assert names == ["a", "b"]
    first, second = api.requests
    assert first.url.params["limit"] == "500" and "continue" not in first.url.params
    assert second.url.params["continue"] == "page2"
    assert first.headers["authorization"] == "Bearer t1"
    assert second.headers["authorization"] == "Bearer t2"  # re-read each call
    assert {r.method for r in api.requests} == {"GET"}


def test_every_kind_has_a_lister_and_metrics_parse() -> None:
    api = FakeApiServer()
    listers = default_listers(api.kube())
    assert set(listers) == set(LIST_PATHS)
    samples = usage_samples(Collector(listers, identity))
    assert samples == [
        {"namespace": "shop", "pod": "a", "container": "web", "cpu": 0.25, "memory": 0.5}
    ]


def test_tasks_read_logs_and_describe_without_secrets() -> None:
    api = FakeApiServer()
    tasks = default_task_api(api.kube())
    logs = run({"kind": "logs", "params": {"namespace": "shop", "pod": "a", "tail_lines": 900}},
               tasks, lambda ns: True)  # fmt: skip
    assert "started" in logs
    q = api.requests[-1].url.params
    assert q["tailLines"] == "500" and q["timestamps"] == "true" and "previous" not in q
    describe = {"kind": "describe", "params": {"namespace": "shop", "kind": "Pod", "name": "a"}}
    described = run(describe, tasks, lambda ns: True)
    assert "Pod shop/a" in described and "hunter2" not in described
    assert api.requests[-1].url.params["fieldSelector"] == "involvedObject.name=a"


def test_version_and_errors() -> None:
    api = FakeApiServer()
    kube = api.kube()
    assert kube.version() == "v1.34.1"
    with pytest.raises(KubeError) as err:
        kube.lister("Node")()
    assert err.value.status == 403


def test_in_cluster_needs_the_service_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    with pytest.raises(KubeError, match="not running inside"):
        Kube.in_cluster()
