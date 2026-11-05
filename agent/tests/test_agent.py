from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from costlens_agent import trim
from costlens_agent.api import CostLensApi, TokenRejected
from costlens_agent.collect import Collector, provider_of
from costlens_agent.config import AgentConfig, ConfigError, from_env
from costlens_agent.main import SNAPSHOT_KINDS, Agent

SECRET = "postgres://admin:hunter2@db:5432/shop"


def make_pod(uid: str, name: str, ns: str = "shop") -> dict[str, Any]:
    return {
        "metadata": {
            "uid": uid,
            "name": name,
            "namespace": ns,
            "labels": {"app": "web"},
            "annotations": {"kubectl.kubernetes.io/last-applied-configuration": SECRET},
            "ownerReferences": [
                {"kind": "ReplicaSet", "name": "web-abc", "uid": "rs1", "controller": True}
            ],
        },
        "spec": {
            "nodeName": "node-1",
            "containers": [
                {
                    "name": "web",
                    "image": "shop/web:1.2",
                    "env": [{"name": "DATABASE_URL", "value": SECRET}],
                    "command": ["sh", "-c", f"run --db {SECRET}"],
                    "args": [SECRET],
                    "resources": {
                        "requests": {"cpu": "250m", "memory": "256Mi"},
                        "limits": {"memory": "512Mi"},
                    },
                }
            ],
        },
        "status": {
            "phase": "Running",
            "qosClass": "Burstable",
            "containerStatuses": [
                {
                    "name": "web",
                    "ready": False,
                    "restartCount": 7,
                    "state": {"waiting": {"reason": "CrashLoopBackOff", "message": SECRET}},
                    "lastState": {"terminated": {"reason": "OOMKilled", "exitCode": 137}},
                }
            ],
        },
    }


def test_pod_trim_keeps_what_matters_and_drops_secrets() -> None:
    record = trim.to_record("Pod", make_pod("p1", "web-1"))
    assert SECRET not in json.dumps(record)
    data = record["data"]
    assert data["containers"] == [
        {
            "name": "web",
            "image": "shop/web:1.2",
            "resources": {
                "requests": {"cpu": "250m", "memory": "256Mi"},
                "limits": {"memory": "512Mi"},
            },
        }
    ]
    assert data["owners"] == [{"kind": "ReplicaSet", "name": "web-abc", "uid": "rs1"}]
    status = data["container_statuses"][0]
    assert status["restartCount"] == 7
    assert status["current"] == {"state": "waiting", "reason": "CrashLoopBackOff"}
    assert status["last"] == {"state": "terminated", "reason": "OOMKilled", "exitCode": 137}


def test_every_kind_trims_and_template_env_is_dropped() -> None:
    template = {
        "template": {
            "spec": {"containers": [{"name": "a", "image": "x", "env": [{"value": SECRET}]}]}
        }
    }
    samples = {
        "Node": {
            "spec": {"providerID": "aws:///eu-west-1a/i-1"},
            "status": {
                "capacity": {"cpu": "4"},
                "nodeInfo": {"kubeletVersion": "v1.33.2", "bootID": "x"},
            },
        },
        "Deployment": {"spec": {"replicas": 3, **template}, "status": {"readyReplicas": 2}},
        "Job": {"spec": {"backoffLimit": 6, **template}, "status": {"failed": 1}},
        "CronJob": {"spec": {"schedule": "*/5 * * * *", "jobTemplate": {"spec": template}}},
        "Service": {"spec": {"type": "ClusterIP", "ports": [{"port": 80, "name": "http"}]}},
        "PersistentVolumeClaim": {"spec": {"resources": {"requests": {"storage": "10Gi"}}}},
        "HorizontalPodAutoscaler": {
            "spec": {
                "minReplicas": 2,
                "maxReplicas": 9,
                "scaleTargetRef": {"kind": "Deployment", "name": "web"},
            }
        },
        "Namespace": {"status": {"phase": "Active"}},
    }
    for kind, body in samples.items():
        record = trim.to_record(kind, {"metadata": {"uid": "u", "name": "n"}, **body})
        assert SECRET not in json.dumps(record), kind
    assert set(trim.TRIMMERS) == set(SNAPSHOT_KINDS)


def page(items: list, cont: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(items=items, metadata=SimpleNamespace(_continue=cont))


def test_collector_paginates_and_filters_namespaces() -> None:
    calls: list[dict] = []

    def list_pods(**kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        if "_continue" not in kwargs:
            return page([make_pod("p1", "a"), make_pod("p2", "b", "kube-system")], cont="next")
        return page([make_pod("p3", "c", "billing")])

    c = Collector({"Pod": list_pods}, serialize=lambda x: x)
    c.configure([], ["kube-system"])
    assert [r["name"] for r in c.objects("Pod")] == ["a", "c"]
    assert calls[1]["_continue"] == "next" and calls[0]["limit"] == 500
    c.configure(["billing"], [])
    assert [r["name"] for r in c.objects("Pod")] == ["c"]
    assert provider_of([{"data": {"providerID": "gce://p/z/n"}}]) == "gke"
    assert provider_of([]) is None


CONFIG = AgentConfig(
    url="https://costlens.example", token="ckt_abc_def", cluster_name="prod", chunk_size=2
)


def recorder(responses: list[int] | None = None):
    sent: list[dict] = []
    codes = list(responses or [])

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(
            {
                "path": request.url.path,
                "auth": request.headers["authorization"],
                "body": json.loads(request.content),
            }
        )
        status = codes.pop(0) if codes else 200
        return httpx.Response(
            status, json={"exclude_namespaces": ["kube-system"], "snapshot_interval_seconds": 300}
        )

    return sent, httpx.MockTransport(handle)


def test_snapshot_chunks_mark_only_the_last_final_and_empty_kinds_still_report() -> None:
    sent, transport = recorder()
    api = CostLensApi(CONFIG, transport=transport, sleep=lambda _: None)
    objects = [{"uid": str(i), "name": f"p{i}", "data": {}} for i in range(5)]
    assert api.snapshot("Pod", "snap-1", iter(objects)) == 5
    assert [len(s["body"]["objects"]) for s in sent] == [2, 2, 1]
    assert [s["body"]["final"] for s in sent] == [False, False, True]
    assert all(s["auth"] == "Bearer ckt_abc_def" for s in sent)
    sent.clear()
    api.snapshot("Job", "snap-1", iter([]))
    assert sent[0]["body"] == {"kind": "Job", "snapshot_id": "snap-1", "final": True, "objects": []}


def test_retries_outages_and_stops_on_revoked_token() -> None:
    waits: list[float] = []
    sent, transport = recorder([503, 429, 200])
    api = CostLensApi(CONFIG, transport=transport, sleep=waits.append)
    api.events([{"uid": "e"}])
    assert len(sent) == 3 and waits == [1.0, 2.0]
    _, transport = recorder([401])
    with pytest.raises(TokenRejected):
        CostLensApi(CONFIG, transport=transport, sleep=lambda _: None).hello("v1.33", "eks")
    _, transport = recorder([500, 500, 500, 500])
    with pytest.raises(httpx.HTTPStatusError):
        CostLensApi(CONFIG, transport=transport, sleep=lambda _: None).hello(None, None)


def test_agent_tick_sends_snapshot_then_only_new_or_changed_events() -> None:
    sent, transport = recorder()
    event = {
        "metadata": {"uid": "e1", "namespace": "shop"},
        "type": "Warning",
        "reason": "BackOff",
        "message": "Back-off",
        "count": 1,
        "involvedObject": {"kind": "Pod", "name": "a"},
        "lastTimestamp": "2026-11-05T10:00:00Z",
    }
    system_event = {**event, "metadata": {"uid": "e2", "namespace": "kube-system"}}
    events = [event, system_event]
    listers: dict[str, Any] = {kind: (lambda **_: page([])) for kind in SNAPSHOT_KINDS}
    listers["Pod"] = lambda **_: page([make_pod("p1", "a")])
    listers["Event"] = lambda **_: page(events)
    now = [0.0]
    agent = Agent(
        CostLensApi(CONFIG, transport=transport, sleep=lambda _: None),
        Collector(listers, serialize=lambda x: x),
        lambda: "v1.33.2",
        clock=lambda: now[0],
    )
    wait = agent.tick()
    paths = [s["path"] for s in sent]
    assert paths[0] == "/api/v1/agent/hello" and sent[0]["body"]["kubernetes_version"] == "v1.33.2"
    assert paths.count("/api/v1/agent/snapshot") == len(SNAPSHOT_KINDS)
    event_posts = [s for s in sent if s["path"] == "/api/v1/agent/events"]
    assert [e["uid"] for e in event_posts[0]["body"]["events"]] == ["e1"]  # kube-system skipped
    assert wait == 60.0
    sent.clear()
    now[0] = 61.0
    agent.tick()
    assert sent == []  # nothing changed, snapshot not due yet
    event["count"] = 4
    now[0] = 130.0
    agent.tick()
    assert [s["body"]["events"][0]["count"] for s in sent] == [4]


def test_config_from_env(tmp_path) -> None:
    token = tmp_path / "token"
    token.write_text("ckt_a_b\n")
    cfg = from_env({"COSTLENS_URL": "https://c.example/", "COSTLENS_TOKEN_FILE": str(token)})
    assert cfg.url == "https://c.example" and cfg.token == "ckt_a_b"
    with pytest.raises(ConfigError):
        from_env({"COSTLENS_URL": "http://c.example", "COSTLENS_TOKEN": "ckt_a_b"})
    assert (
        from_env(
            {
                "COSTLENS_URL": "http://c.example",
                "COSTLENS_TOKEN": "ckt_a_b",
                "COSTLENS_ALLOW_HTTP": "true",
            }
        ).url
        == "http://c.example"
    )
    with pytest.raises(ConfigError):
        from_env({"COSTLENS_URL": "https://c.example", "COSTLENS_TOKEN": "wrong"})
