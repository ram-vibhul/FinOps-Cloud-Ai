from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from ccradar_agent import trim
from ccradar_agent.api import CloudCostRadarApi, TokenRejected
from ccradar_agent.collect import Collector, provider_of
from ccradar_agent.config import AgentConfig, ConfigError, from_env
from ccradar_agent.main import SNAPSHOT_KINDS, Agent

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


def test_pod_ip_kept_unless_redacting(monkeypatch) -> None:
    pod = make_pod("p1", "web-1")
    pod["status"]["podIP"] = "10.1.2.3"
    assert trim.to_record("Pod", pod)["data"]["podIP"] == "10.1.2.3"
    monkeypatch.setattr(trim, "REDACT_IPS", True)
    assert "podIP" not in trim.to_record("Pod", pod)["data"]


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
    url="https://ccradar.example", token="ckt_abc_def", cluster_name="prod", chunk_size=2
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
    api = CloudCostRadarApi(CONFIG, transport=transport, sleep=lambda _: None)
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
    api = CloudCostRadarApi(CONFIG, transport=transport, sleep=waits.append)
    api.events([{"uid": "e"}])
    assert len(sent) == 3 and waits == [1.0, 2.0]
    _, transport = recorder([401])
    with pytest.raises(TokenRejected):
        CloudCostRadarApi(CONFIG, transport=transport, sleep=lambda _: None).hello("v1.33", "eks")
    _, transport = recorder([500, 500, 500, 500])
    with pytest.raises(httpx.HTTPStatusError):
        CloudCostRadarApi(CONFIG, transport=transport, sleep=lambda _: None).hello(None, None)


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
        CloudCostRadarApi(CONFIG, transport=transport, sleep=lambda _: None),
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
    cfg = from_env({"CCRADAR_URL": "https://c.example/", "CCRADAR_TOKEN_FILE": str(token)})
    assert cfg.url == "https://c.example" and cfg.token == "ckt_a_b"
    with pytest.raises(ConfigError):
        from_env({"CCRADAR_URL": "http://c.example", "CCRADAR_TOKEN": "ckt_a_b"})
    assert (
        from_env(
            {
                "CCRADAR_URL": "http://c.example",
                "CCRADAR_TOKEN": "ckt_a_b",
                "CCRADAR_ALLOW_HTTP": "true",
            }
        ).url
        == "http://c.example"
    )
    with pytest.raises(ConfigError):
        from_env({"CCRADAR_URL": "https://c.example", "CCRADAR_TOKEN": "wrong"})


def test_usage_samples_from_metrics_server_and_pause_without_it() -> None:
    from ccradar_agent.collect import usage_samples

    metrics = {
        "items": [
            {
                "metadata": {"name": "web-1", "namespace": "shop"},
                "containers": [
                    {"name": "web", "usage": {"cpu": "250000000n", "memory": "524288Ki"}}
                ],
            },
            {
                "metadata": {"name": "dns", "namespace": "kube-system"},
                "containers": [{"name": "coredns", "usage": {"cpu": "5m", "memory": "20Mi"}}],
            },
        ],
        "metadata": {},
    }
    c = Collector({"PodMetrics": lambda **_: metrics}, serialize=lambda x: x)
    c.configure([], ["kube-system"])
    assert usage_samples(c) == [
        {"namespace": "shop", "pod": "web-1", "container": "web", "cpu": 0.25, "memory": 0.5}
    ]
    sent, transport = recorder()
    agent = Agent(
        CloudCostRadarApi(CONFIG, transport=transport, sleep=lambda _: None),
        c,
        lambda: None,
        clock=lambda: 100.0,
    )
    agent.wall_clock = lambda: "2026-11-05T10:00:00+00:00"
    assert agent.usage() == 1
    assert sent[0]["path"] == "/api/v1/agent/usage"
    assert sent[0]["body"]["sampled_at"] == "2026-11-05T10:00:00+00:00"

    def missing(**_: Any) -> Any:
        raise RuntimeError("404 metrics.k8s.io not found")

    broken = Agent(
        CloudCostRadarApi(CONFIG, transport=transport, sleep=lambda _: None),
        Collector({"PodMetrics": missing}, serialize=lambda x: x),
        lambda: None,
        clock=lambda: 100.0,
    )
    assert broken.usage() == 0 and broken.next_usage == 1900.0


# Assembled from pieces so secret scanners do not mistake the test for a leak; not real.
FAKE_GITHUB_TOKEN = "gh" + "p_" + "aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789"
LEAKY = [
    "connect postgres://app:S3cr3t!pass@db.shop.svc:5432/orders failed",
    "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
    "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
    "using AKIAIOSFODNN7EXAMPLE",
    'env {"DB_PASSWORD": "correct horse battery"}',
    "token " + FAKE_GITHUB_TOKEN + " rejected",
    "user ravi@acme.example called +91 98765 43210",
]


@pytest.mark.parametrize("message", LEAKY)
def test_event_messages_are_redacted_inside_the_cluster(message: str) -> None:
    obj = {
        "metadata": {"uid": "e1", "namespace": "shop"},
        "type": "Warning",
        "reason": "Failed",
        "message": message,
        "lastTimestamp": "2026-11-05T10:00:00Z",
    }
    record = trim.event(obj)
    assert record is not None
    for secret in (
        "S3cr3t!pass",
        "eyJhbGciOiJIUzI1NiJ9",
        "AKIAIOSFODNN7EXAMPLE",
        "correct horse",
        FAKE_GITHUB_TOKEN,
        "ravi@acme.example",
        "98765",
    ):
        assert secret not in record["message"]


def test_ip_redaction_is_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    obj = {
        "metadata": {"uid": "e1"},
        "message": "dial tcp 10.20.3.4:5432: refused",
        "lastTimestamp": "2026-11-05T10:00:00Z",
    }
    assert "10.20.3.4" in trim.event(obj)["message"]  # type: ignore[index]
    monkeypatch.setattr(trim, "REDACT_IPS", True)
    assert "10.20.3.4" not in trim.event(obj)["message"]  # type: ignore[index]


def task_api(calls: list) -> Any:
    from ccradar_agent.tasks import TaskApi

    def read_log(name, namespace, **kw):
        calls.append(("log", name, namespace, kw))
        return (
            "2026-11-05T10:00:00Z boot\n"
            "2026-11-05T10:00:01Z db=postgres://app:S3cr3t@db:5432/x refused\n"
        )

    def read_pod(name, namespace):
        calls.append(("read", name, namespace))
        return make_pod("p1", name, namespace)

    def events(namespace, field_selector):
        calls.append(("events", namespace, field_selector))
        return {
            "items": [
                {
                    "metadata": {"uid": "e"},
                    "type": "Warning",
                    "reason": "BackOff",
                    "message": "Back-off restarting",
                    "count": 4,
                    "lastTimestamp": "2026-11-05T10:00:00Z",
                }
            ]
        }

    return TaskApi(read_log=read_log, read={"Pod": read_pod}, events=events, serialize=lambda x: x)


def test_logs_and_describe_tasks_are_capped_redacted_and_trimmed() -> None:
    from ccradar_agent.tasks import run

    calls: list = []
    api = task_api(calls)
    out = run(
        {
            "kind": "logs",
            "params": {
                "namespace": "shop",
                "pod": "web-1",
                "container": "web",
                "previous": True,
                "tail_lines": 5000,
            },
        },
        api,
        lambda ns: True,
    )
    assert "S3cr3t" not in out and "postgres://[REDACTED]@db" in out
    assert calls[0][3] == {
        "tail_lines": 500,
        "previous": True,
        "timestamps": True,
        "container": "web",
    }
    described = run(
        {"kind": "describe", "params": {"namespace": "shop", "kind": "Pod", "name": "web-1"}},
        api,
        lambda ns: True,
    )
    assert described.startswith("Pod shop/web-1") and "CrashLoopBackOff" in described
    assert SECRET not in described and "BackOff" in described  # env never included
    assert ("events", "shop", "involvedObject.name=web-1") in calls


@pytest.mark.parametrize(
    ("task", "why"),
    [
        ({"kind": "exec", "params": {"namespace": "shop"}}, "not allowed"),
        ({"kind": "logs", "params": {"namespace": "shop", "pod": "web; rm -rf /"}}, "invalid pod"),
        ({"kind": "logs", "params": {"namespace": "kube-system", "pod": "coredns"}}, "not watched"),
        (
            {"kind": "describe", "params": {"namespace": "shop", "kind": "Secret", "name": "db"}},
            "cannot be described",
        ),
    ],
)
def test_tasks_outside_the_allow_list_are_refused(task: dict, why: str) -> None:
    from ccradar_agent.tasks import TaskRefused, run

    with pytest.raises(TaskRefused, match=why):
        run(task, task_api([]), lambda ns: ns != "kube-system")


def test_logs_can_be_turned_off(monkeypatch: pytest.MonkeyPatch) -> None:
    from ccradar_agent import tasks

    monkeypatch.setattr(tasks, "ALLOW_LOGS", False)
    with pytest.raises(tasks.TaskRefused, match="turned off"):
        tasks.run(
            {"kind": "logs", "params": {"namespace": "shop", "pod": "web-1"}},
            task_api([]),
            lambda ns: True,
        )


def test_agent_runs_claimed_tasks_and_reports_results() -> None:
    posted: list[dict] = []

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        posted.append({"path": request.url.path, "body": body})
        if request.url.path.endswith("/tasks/claim"):
            return httpx.Response(
                200,
                json={
                    "tasks": [
                        {
                            "id": "t1",
                            "kind": "logs",
                            "params": {"namespace": "shop", "pod": "web-1"},
                        },
                        {"id": "t2", "kind": "exec", "params": {"namespace": "shop"}},
                    ]
                },
            )
        return httpx.Response(200, json={"status": "done"})

    api = CloudCostRadarApi(CONFIG, transport=httpx.MockTransport(handle), sleep=lambda _: None)
    agent = Agent(api, Collector({}, serialize=lambda x: x), lambda: None, task_api=task_api([]))
    assert agent.tasks() == 1
    results = {p["path"].split("/")[-2]: p["body"] for p in posted if p["path"].endswith("/result")}
    assert results["t1"]["ok"] and "boot" in results["t1"]["text"]
    assert results["t2"] == {
        "ok": False,
        "text": None,
        "error": "Refused by the agent: task kind not allowed",
    }


def prom_transport(fail: bool = False, seen: list | None = None):
    def handle(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if fail:
            return httpx.Response(503, text="down")
        q = request.url.params["query"]
        if "cpu" in q:
            result = [
                {
                    "metric": {"namespace": "shop", "pod": "web-1", "container": "web"},
                    "value": [1, "0.25"],
                },
                {
                    "metric": {"namespace": "kube-system", "pod": "dns", "container": "coredns"},
                    "value": [1, "0.01"],
                },
                {
                    "metric": {"namespace": "shop", "pod": "web-2", "container": "web"},
                    "value": [1, "NaN"],
                },
            ]
        else:
            result = [
                {
                    "metric": {"namespace": "shop", "pod": "web-1", "container": "web"},
                    "value": [1, str(512 * 2**20)],
                }
            ]
        return httpx.Response(
            200, json={"status": "success", "data": {"resultType": "vector", "result": result}}
        )

    return httpx.MockTransport(handle)


def test_prometheus_usage_samples(tmp_path) -> None:
    from ccradar_agent.prometheus import Prometheus, from_env

    token = tmp_path / "token"
    token.write_text("prom-secret\n")
    seen: list = []
    prom = Prometheus(
        "http://prometheus.monitoring:9090/",
        token_file=str(token),
        http=httpx.Client(transport=prom_transport(seen=seen)),
    )
    samples = prom.usage_samples(lambda ns: ns != "kube-system")
    assert samples == [
        {"namespace": "shop", "pod": "web-1", "container": "web", "cpu": 0.25, "memory": 0.5}
    ]
    assert seen[0].headers["authorization"] == "Bearer prom-secret"
    assert str(seen[0].url).startswith("http://prometheus.monitoring:9090/api/v1/query?query=")
    assert from_env({}) is None
    assert from_env({"CCRADAR_PROMETHEUS_URL": "http://p:9090"}) is not None


def test_agent_prefers_prometheus_and_falls_back_to_metrics_server() -> None:
    from ccradar_agent.prometheus import Prometheus

    sent, transport = recorder()
    metrics = {
        "items": [
            {
                "metadata": {"name": "web-1", "namespace": "shop"},
                "containers": [{"name": "web", "usage": {"cpu": "100m", "memory": "256Mi"}}],
            }
        ]
    }
    collector = Collector({"PodMetrics": lambda **_: metrics}, serialize=lambda x: x)
    good = Prometheus("http://p", http=httpx.Client(transport=prom_transport()))
    agent = Agent(
        CloudCostRadarApi(CONFIG, transport=transport, sleep=lambda _: None),
        collector,
        lambda: None,
        prometheus=good,
    )
    agent.usage()
    assert agent.usage_source == "prometheus" and sent[-1]["body"]["samples"][0]["cpu"] == 0.25
    down = Prometheus("http://p", http=httpx.Client(transport=prom_transport(fail=True)))
    agent = Agent(
        CloudCostRadarApi(CONFIG, transport=transport, sleep=lambda _: None),
        collector,
        lambda: None,
        prometheus=down,
    )
    agent.usage()
    assert agent.usage_source == "metrics-server" and sent[-1]["body"]["samples"][0]["cpu"] == 0.1


def test_prometheus_error_status_raises() -> None:
    from ccradar_agent.prometheus import Prometheus

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "error", "error": "parse error"})

    prom = Prometheus("http://p", http=httpx.Client(transport=httpx.MockTransport(handle)))
    with pytest.raises(RuntimeError, match="parse error"):
        prom.usage_samples(lambda ns: True)


def svc(name: str, ns: str, port: int = 9090, labels: dict | None = None) -> dict:
    return {
        "name": name,
        "namespace": ns,
        "data": {"labels": labels or {}, "ports": [{"port": port}]},
    }


def test_prometheus_discovery_candidates_and_probe() -> None:
    from ccradar_agent.prometheus import candidates, discover

    services = [
        svc("alertmanager-operated", "monitoring", 9093),
        svc("my-prom", "observability", 80, {"app.kubernetes.io/name": "prometheus"}),
        svc("prometheus-operated", "monitoring"),
        svc(
            "prometheus-node-exporter", "monitoring", 9100, {"app.kubernetes.io/name": "prometheus"}
        ),
        svc("web", "shop", 80),
    ]
    assert candidates(services) == [
        "http://prometheus-operated.monitoring.svc:9090",
        "http://my-prom.observability.svc:80",
    ]

    def probe(url: str) -> bool:
        if "operated" in url:
            raise httpx.ConnectError("refused")
        return True

    assert discover(services, probe) == "http://my-prom.observability.svc:80"
    assert discover([svc("web", "shop")], probe) is None


def test_agent_discovers_and_reports_prometheus() -> None:
    sent, transport = recorder()
    listers: dict[str, Any] = {kind: (lambda **_: page([])) for kind in SNAPSHOT_KINDS}
    listers["Service"] = lambda **_: page(
        [
            {
                "metadata": {"uid": "s", "name": "prometheus-server", "namespace": "mon"},
                "spec": {"ports": [{"port": 80}]},
            }
        ]
    )
    agent = Agent(
        CloudCostRadarApi(CONFIG, transport=transport, sleep=lambda _: None),
        Collector(listers, serialize=lambda x: x),
        lambda: "v1.33",
        clock=lambda: 0.0,
    )
    agent.probe = lambda url: True
    agent.hello()
    assert (
        agent.prometheus is not None
        and agent.prometheus.url == "http://prometheus-server.mon.svc:80"
    )
    hello = next(s["body"] for s in sent if s["path"] == "/api/v1/agent/hello")
    assert hello["usage_source"] == "prometheus" and hello["prometheus_discovered"] is True


def test_customer_queries_are_rechecked_run_and_reported() -> None:
    from ccradar_agent.prometheus import Prometheus

    def handle(request: httpx.Request) -> httpx.Response:
        q = request.url.params["query"]
        if "boom" in q:
            return httpx.Response(200, json={"status": "error", "error": "bad_data: parse error"})
        if q.startswith("scalar"):
            return httpx.Response(
                200,
                json={"status": "success", "data": {"resultType": "scalar", "result": [1, "42"]}},
            )
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "resultType": "vector",
                    "result": [
                        {"metric": {"queue": "orders"}, "value": [1, "12"]},
                        {"metric": {"queue": "mail"}, "value": [1, "30"]},
                    ],
                },
            },
        )

    sent, transport = recorder()
    agent = Agent(
        CloudCostRadarApi(CONFIG, transport=transport, sleep=lambda _: None),
        Collector({}, serialize=lambda x: x),
        lambda: None,
        prometheus=Prometheus("http://p", http=httpx.Client(transport=httpx.MockTransport(handle))),
    )
    agent.config = {
        "metric_queries": [
            {"id": "q1", "promql": "sum by (queue) (queue_depth)"},
            {"id": "q2", "promql": "scalar(sum(up))"},
            {"id": "q3", "promql": "rate(x[30d])"},
            {"id": "q4", "promql": "boom()"},
        ]
    }
    assert agent.metrics() == 2
    posted = {
        s["body"]["query_id"]: s["body"] for s in sent if s["path"] == "/api/v1/agent/metrics"
    }
    assert posted["q1"]["series"] == [
        {"labels": {"queue": "mail"}, "value": 30.0},
        {"labels": {"queue": "orders"}, "value": 12.0},
    ]
    assert posted["q2"]["series"] == [{"labels": {}, "value": 42.0}]
    assert posted["q3"]["ok"] is False and "at most 1d" in posted["q3"]["error"]
    assert posted["q4"]["ok"] is False and "parse error" in posted["q4"]["error"]


def test_promql_test_task() -> None:
    from ccradar_agent.prometheus import Prometheus
    from ccradar_agent.tasks import TaskRefused, run

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "resultType": "vector",
                    "result": [
                        {"metric": {"pod": "web-1", "host": "db.internal"}, "value": [1, "0.5"]}
                    ],
                },
            },
        )

    prom = Prometheus("http://p", http=httpx.Client(transport=httpx.MockTransport(handle)))
    out = run({"kind": "promql", "params": {"promql": "up"}}, task_api([]), lambda ns: True, prom)
    assert out == "0.5  host=db.internal, pod=web-1"
    with pytest.raises(TaskRefused, match="no Prometheus"):
        run({"kind": "promql", "params": {"promql": "up"}}, task_api([]), lambda ns: True, None)
    with pytest.raises(TaskRefused, match="not allowed"):
        run({"kind": "promql", "params": {"promql": "sum(x"}}, task_api([]), lambda ns: True, prom)
