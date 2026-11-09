from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from costlens.accounts.roles import Role
from costlens.db import Database
from costlens.db.base import utcnow
from costlens.db.tenant import set_tenant
from costlens.k8s.investigate import gather, investigate
from costlens.k8s.service import ingest_events, ingest_objects
from costlens.models import ChangeEvent, K8sCluster, K8sUsage
from tests.helpers import add_to_org, make_org, sign_in_client

RS = {"kind": "ReplicaSet", "name": "api-6c", "uid": "rs"}


def status(current=None, last=None, restarts=0, exit_code=None):
    s = {"name": "api", "ready": current is None, "restartCount": restarts}
    if current:
        s["current"] = {"state": "waiting", "reason": current}
    if last:
        s["last"] = {"state": "terminated", "reason": last, "exitCode": exit_code}
    return s


def pod(i, statuses=(), phase="Running", node="node-1", limit=None):
    res = {"requests": {"cpu": "500m", "memory": "512Mi"}}
    if limit:
        res["limits"] = {"memory": limit}
    return {
        "uid": f"p{i}",
        "name": f"api-6c-{i}",
        "namespace": "shop",
        "data": {
            "owners": [RS],
            "nodeName": node if phase != "Pending" else None,
            "phase": phase,
            "containers": [{"name": "api", "image": "acme/api:2.0", "resources": res}],
            "container_statuses": list(statuses),
        },
    }


def event(uid, reason, message, kind="Pod", name="api-6c-0", type_="Warning", count=3):
    now = utcnow()
    return {
        "uid": uid,
        "namespace": "shop",
        "object_kind": kind,
        "object_name": name,
        "type": type_,
        "reason": reason,
        "message": message,
        "count": count,
        "first_at": now - timedelta(minutes=30),
        "last_at": now - timedelta(minutes=1),
    }


def scenario(
    db: Database, pods, events=(), rs_age_minutes=600, nodes=None, usage=None, change=None
):
    org_id, _ = make_org(db, f"Acme {uuid.uuid4().hex[:6]}", f"{uuid.uuid4().hex[:6]}@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        c = K8sCluster(
            id=uuid.uuid4(), org_id=org_id, name="prod", token_hash="x" * 64, token_hint="x"
        )
        s.add(c)
        s.flush()
        created = (utcnow() - timedelta(minutes=rs_age_minutes)).isoformat()
        ingest_objects(
            s,
            c,
            "ReplicaSet",
            "s",
            [
                {
                    "uid": "rs",
                    "name": "api-6c",
                    "namespace": "shop",
                    "data": {
                        "creationTimestamp": created,
                        "owners": [{"kind": "Deployment", "name": "api", "uid": "d"}],
                    },
                }
            ],
            final=True,
        )
        ingest_objects(
            s,
            c,
            "Node",
            "s",
            nodes
            or [
                {
                    "uid": "n1",
                    "name": "node-1",
                    "data": {"conditions": [{"type": "Ready", "status": "True"}]},
                }
            ],
            final=True,
        )
        ingest_objects(s, c, "Pod", "s", pods, final=True)
        if events:
            ingest_events(s, c, events)
        if usage:
            s.add(
                K8sUsage(
                    org_id=org_id,
                    cluster_id=c.id,
                    hour=utcnow().replace(minute=0, second=0, microsecond=0),
                    namespace="shop",
                    kind="Deployment",
                    name="api",
                    container="api",
                    samples=60,
                    cpu_sum=1,
                    cpu_max=usage.get("cpu", 0.1),
                    memory_sum=1,
                    memory_max=usage.get("mem", 0.1),
                    cpu_request=0.5,
                    memory_request=0.5,
                    cpu_limit=usage.get("cpu_limit"),
                    memory_limit=usage.get("mem_limit"),
                    replicas=1,
                )
            )
        if change:
            s.add(
                ChangeEvent(
                    org_id=org_id,
                    occurred_at=utcnow() - timedelta(minutes=40),
                    source="github",
                    kind="deploy",
                    title=change,
                    external_id=uuid.uuid4().hex,
                )
            )
        s.flush()
        ev = gather(
            s,
            c,
            namespace="shop",
            kind="Deployment",
            name="api",
            start=utcnow() - timedelta(hours=6),
            end=utcnow(),
        )
        return investigate(ev, "prod")


def top(result):
    return result["causes"][0]["check"] if result["causes"] else None


def test_oom_kill_ranks_first_with_memory_evidence(db: Database) -> None:
    r = scenario(
        db,
        [pod(0, [status("CrashLoopBackOff", "OOMKilled", 9, 137)], limit="512Mi")],
        [
            event("e1", "OOMKilling", "Memory cgroup out of memory: Killed process 42 (java)"),
            event("e2", "BackOff", "Back-off restarting failed container api"),
        ],
        usage={"mem": 0.5, "mem_limit": 0.5},
    )
    assert top(r) == "oom" and r["causes"][0]["confidence"] >= 0.9
    assert "crash_loop" not in [c["check"] for c in r["causes"]]  # explained by OOM
    evidence = " ".join(r["causes"][0]["evidence"])
    assert "OOMKilled" in evidence and "memory peak 0.50 GiB vs limit 0.50 GiB" in evidence
    assert any("Raise the memory limit to at least 0.65 GiB" in f for f in r["causes"][0]["fixes"])
    assert r["causes"][0]["verify"] and r["summary"].startswith("Most likely: out of memory")


def test_crash_loop_after_rollout_suggests_rollback(db: Database) -> None:
    r = scenario(
        db,
        [pod(0, [status("CrashLoopBackOff", "Error", 6, 127)])],
        [event("e1", "BackOff", "Back-off restarting failed container api")],
        rs_age_minutes=45,
        change="Deploy api v2.0 to shop",
    )
    cause = r["causes"][0]
    assert cause["check"] == "crash_loop" and cause["confidence"] == 0.85
    assert any("Exit code 127" in e and "not found" in e for e in cause["evidence"])
    assert any("binary is missing" in f for f in cause["fixes"])
    assert any("rollout undo" in f for f in cause["fixes"])
    assert any(e.startswith("Started after: Rollout") for e in cause["evidence"])
    assert any("Deploy api v2.0" in c for c in r["facts"]["changes"])


@pytest.mark.parametrize(
    ("pods", "events", "expected", "fix_hint"),
    [
        (
            [pod(0, [status("ImagePullBackOff")])],
            [
                event(
                    "e",
                    "Failed",
                    "Failed to pull image acme/api:2.0: unauthorized: authentication required",
                )
            ],
            "image_pull",
            "imagePullSecrets",
        ),
        (
            [pod(0, [status("CreateContainerConfigError")])],
            [event("e", "Failed", 'Error: configmap "api-config" not found')],
            "config",
            "ConfigMap",
        ),
        (
            [pod(0, phase="Pending")],
            [event("e", "FailedScheduling", "0/3 nodes are available: 3 Insufficient memory.")],
            "unschedulable",
            "lower requests",
        ),
        (
            [pod(0)],
            [
                event(
                    "e",
                    "Unhealthy",
                    "Liveness probe failed: HTTP probe failed with statuscode: 500",
                )
            ],
            "probes",
            "startupProbe",
        ),
        (
            [pod(0)],
            [event("e", "FailedMount", "MountVolume.SetUp failed for volume data")],
            "volume",
            "Bound",
        ),
        (
            [pod(0)],
            [event("e", "Failed", "dial tcp: lookup db.shop.svc on 10.0.0.10:53: no such host")],
            "dns",
            "CoreDNS",
        ),
    ],
)
def test_known_failures(db: Database, pods, events, expected, fix_hint) -> None:
    r = scenario(db, pods, events)
    assert top(r) == expected
    assert any(fix_hint in f for f in r["causes"][0]["fixes"])


def test_node_pressure_and_cpu_throttling(db: Database) -> None:
    r = scenario(
        db,
        [pod(0)],
        nodes=[
            {
                "uid": "n1",
                "name": "node-1",
                "data": {
                    "conditions": [
                        {"type": "MemoryPressure", "status": "True"},
                        {"type": "Ready", "status": "True"},
                    ]
                },
            }
        ],
        usage={"cpu": 0.98, "cpu_limit": 1.0},
    )
    checks = [c["check"] for c in r["causes"]]
    assert checks == ["node", "cpu_throttling"]


def test_healthy_workload(db: Database) -> None:
    r = scenario(db, [pod(0), pod(1)])
    assert r["causes"] == [] and r["summary"].startswith("No known failure pattern")
    assert r["facts"]["running"] == 2 and [p["name"] for p in r["facts"]["pod_list"]] == [
        "api-6c-0",
        "api-6c-1",
    ]


def test_api_workloads_run_save_refresh_and_isolation(client: TestClient, db: Database) -> None:
    from costlens.demo.seed import ensure_demo, load_all_seeders

    load_all_seeders()
    with db.session() as s:
        demo_id = ensure_demo(s).id
    _, visitor = make_org(db, "Visitor", "visitor@x.com")
    add_to_org(db, demo_id, "visitor@x.com", Role.VIEWER)
    v = sign_in_client(client, db, visitor, demo_id)
    cluster_id = v.get("/api/v1/clusters").json()[0]["id"]
    wl = v.get("/api/v1/k8s/workloads", params={"cluster_id": cluster_id}).json()
    assert wl[0]["name"] == "nightly-etl" and wl[0]["health"] == "failing"
    assert "CrashLoopBackOff" in wl[0]["reasons"] and {w["health"] for w in wl[1:]} == {"ok"}
    body = {
        "cluster_id": cluster_id,
        "namespace": "data",
        "kind": "CronJob",
        "name": "nightly-etl",
        "hours": 12,
    }
    r = v.post("/api/v1/k8s/investigations/run", json=body).json()  # read-only: allowed in demo
    assert r["causes"][0]["check"] == "oom" and r["timeline"]
    assert any("memory peak 2.95 GiB vs limit 3.00 GiB" in e for e in r["causes"][0]["evidence"])
    assert any("at least 3.90 GiB" in f for f in r["causes"][0]["fixes"])
    assert v.post("/api/v1/k8s/investigations", json=body).status_code == 403  # demo is read-only
    # Own organisation: members save, viewers cannot; others cannot see it.
    org_id, owner = make_org(db, "Acme", "owner@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        c = K8sCluster(
            id=uuid.uuid4(), org_id=org_id, name="prod", token_hash="x" * 64, token_hint="x"
        )
        s.add(c)
        s.flush()
        ingest_objects(
            s, c, "Pod", "s", [pod(0, [status("CrashLoopBackOff", "Error", 4, 1)])], final=True
        )
        cid = str(c.id)
    viewer = add_to_org(db, org_id, "viewer@acme.com", Role.VIEWER)
    assert (
        sign_in_client(client, db, viewer, org_id)
        .post("/api/v1/k8s/investigations", json={"cluster_id": cid, "namespace": "shop"})
        .status_code
        == 403
    )
    member = add_to_org(db, org_id, "dev@acme.com", Role.MEMBER)
    m = sign_in_client(client, db, member, org_id)
    saved = m.post("/api/v1/k8s/investigations", json={"cluster_id": cid, "namespace": "shop"})
    assert saved.status_code == 201 and saved.json()["result"]["causes"][0]["check"] == "crash_loop"
    iid = saved.json()["id"]
    assert (
        m.get("/api/v1/k8s/investigations")
        .json()[0]["title"]
        .startswith("namespace shop: Most likely")
    )
    assert m.post(f"/api/v1/k8s/investigations/{iid}/refresh").status_code == 200
    assert m.get(f"/api/v1/k8s/investigations/{iid}").json()["cluster"] == "prod"
    o = sign_in_client(client, db, owner, org_id)
    assert o.get(f"/api/v1/k8s/investigations/{iid}").status_code == 200
    v = sign_in_client(client, db, visitor, demo_id)  # the demo org cannot see Acme's
    assert v.get(f"/api/v1/k8s/investigations/{iid}").status_code == 404


SECRET_URL = "postgres://app:Sup3rSecret@db.shop.svc:5432/orders"


def test_narrative_status_update_and_redaction(db: Database) -> None:
    r = scenario(
        db,
        [pod(0, [status("CrashLoopBackOff", "Error", 6, 1)])],
        [
            event("e1", "BackOff", f"Back-off restarting failed container api: dial {SECRET_URL}"),
            event("e2", "Failed", "user ravi@acme.example token=abcd1234efgh"),
        ],
    )
    text = str(r)
    assert (
        "Sup3rSecret" not in text and "ravi@acme.example" not in text and "abcd1234efgh" not in text
    )
    assert "postgres://[REDACTED]@db.shop.svc" in text  # context kept for debugging
    assert r["worded_by"] == "template"
    assert r["narrative"].startswith(
        "In the last 6 hours, 1 of 1 pod(s) of Deployment api were running"
    )
    assert "The most likely cause is: the container keeps crashing on start-up" in r["narrative"]
    lines = r["status_update"].splitlines()
    assert lines[0] == "[Investigating] Deployment api (prod / shop)"
    assert lines[2].startswith("Likely cause: The container keeps crashing")
    assert lines[3].startswith("Next step: Read the previous container's logs")
    assert lines[4].startswith("Done when:")
    healthy = scenario(db, [pod(0)])
    assert healthy["status_update"].startswith("[All clear] Deployment api (prod / shop)")


class FakeWriter:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.fail = fail

    def rewrite(self, facts: str, system: str | None = None) -> str | None:
        self.calls.append((facts, system))
        if self.fail:
            return None
        return "AI status" if system and "status update" in system else "AI summary"


def test_ai_wording_only_when_opted_in_and_only_on_redacted_facts(
    client: TestClient, db: Database
) -> None:
    from costlens.models import Organization

    org_id, owner = make_org(db, "Acme", "owner@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        c = K8sCluster(
            id=uuid.uuid4(), org_id=org_id, name="prod", token_hash="x" * 64, token_hint="x"
        )
        s.add(c)
        s.flush()
        ingest_objects(
            s, c, "Pod", "s", [pod(0, [status("CrashLoopBackOff", "Error", 4, 1)])], final=True
        )
        ingest_events(s, c, [event("e1", "BackOff", f"failed: {SECRET_URL}")])
        cid = str(c.id)
    writer = FakeWriter()
    client.app.state.ai = writer  # type: ignore[attr-defined]
    m = sign_in_client(client, db, owner, org_id)
    body = {"cluster_id": cid, "namespace": "shop", "kind": "Deployment", "name": "api"}
    assert m.post("/api/v1/k8s/investigations/run", json=body).json()["worded_by"] == "template"
    assert writer.calls == []  # off by default
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        org = s.get(Organization, org_id)
        assert org is not None
        org.settings = {"alerts": {"ai_explanations": True}}
    r = m.post("/api/v1/k8s/investigations/run", json=body).json()
    assert (
        r["worded_by"] == "ai"
        and r["narrative"] == "AI summary"
        and r["status_update"] == "AI status"
    )
    assert r["template_narrative"].startswith("In the last 6 hours")
    assert len(writer.calls) == 2
    assert all("Sup3rSecret" not in facts for facts, _ in writer.calls)
    client.app.state.ai = FakeWriter(fail=True)  # type: ignore[attr-defined]
    r = m.post("/api/v1/k8s/investigations/run", json=body).json()
    assert r["worded_by"] == "template" and r["status_update"].startswith("[Investigating]")


def test_demo_cause_and_effect_links_deploy_memory_and_ooms(
    client: TestClient, db: Database
) -> None:
    from costlens.demo.seed import ensure_demo, load_all_seeders

    load_all_seeders()
    with db.session() as s:
        demo_id = ensure_demo(s).id
    _, visitor = make_org(db, "Visitor", "visitor@x.com")
    add_to_org(db, demo_id, "visitor@x.com", Role.VIEWER)
    v = sign_in_client(client, db, visitor, demo_id)
    cid = v.get("/api/v1/clusters").json()[0]["id"]
    r = v.post(
        "/api/v1/k8s/investigations/run",
        json={
            "cluster_id": cid,
            "namespace": "data",
            "kind": "CronJob",
            "name": "nightly-etl",
            "hours": 6,
        },
    ).json()
    corr = r["correlation"]
    assert corr["anchor"]["title"].startswith('acme/nightly-etl: workflow "Deploy to production"')
    memory = next(e for e in corr["effects"] if e["signal"] == "memory")
    assert memory["change_pct"] >= 50  # v3.2 roughly doubled memory per replica
    assert any(e["signal"] == "warnings" for e in corr["effects"])
    signals = {e["signal"] for e in corr["effects"]}
    assert "cost" not in signals  # requests unchanged: cost flat
    assert "cpu" not in signals  # only the daily cycle: compared day over day, no effect
    assert corr["story"][0].endswith('workflow "Deploy to production" succeeded on main (4f2a9c1)')
    assert any(line.startswith("→ memory per replica up") for line in corr["story"])
    assert len(corr["series"]["hours"]) >= 24 and len(corr["series"]["memory"]) == len(
        corr["series"]["hours"]
    )
    assert {m["type"] for m in corr["markers"]} == {"change", "incident"}
    assert r["causes"][0]["check"] == "oom"  # the investigation itself still ranks OOM first


def test_correlation_without_changes_or_workload(db: Database) -> None:
    from costlens.k8s.correlate import attach, correlate

    org_id, _ = make_org(db, "Solo", "solo@x.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        c = K8sCluster(
            id=uuid.uuid4(), org_id=org_id, name="prod", token_hash="x" * 64, token_hint="x"
        )
        s.add(c)
        s.flush()
        now = utcnow()
        out = correlate(s, c, "shop", "Deployment", "api", now - timedelta(hours=6), now)
        assert out["anchor"] is None and out["effects"] == [] and out["series"]["hours"] == []
        result: dict = {}
        attach(s, c, result, "shop", None, None, now, now)
        assert "correlation" not in result  # only for a single workload


def test_demo_story_does_not_depend_on_the_weekday(db: Database) -> None:
    """Week-over-week baselines: weekday/weekend rhythm is never reported as an effect."""
    from datetime import UTC, date, datetime, time

    from sqlalchemy import select

    from costlens.demo.seed import ensure_demo, load_all_seeders, reset_demo
    from costlens.k8s.correlate import correlate

    load_all_seeders()
    with db.session() as s:
        ensure_demo(s)
    for offset in range(7):
        today = date(2026, 10, 5) + timedelta(days=offset)  # Monday .. Sunday
        with db.session() as s:
            org = reset_demo(s, today=today)
            set_tenant(s, org_id=org.id)
            cluster = s.scalars(select(K8sCluster)).one()
            end = datetime.combine(today, time(0), UTC)
            out = correlate(
                s, cluster, "data", "CronJob", "nightly-etl", end - timedelta(hours=6), end
            )
            signals = {e["signal"] for e in out["effects"]}
            assert "memory" in signals and "cpu" not in signals and "cost" not in signals, (
                today,
                out["story"],
            )
