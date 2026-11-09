from __future__ import annotations

import uuid
from datetime import timedelta
from typing import cast

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from costlens.accounts.roles import Role
from costlens.db import Database
from costlens.db.base import utcnow
from costlens.db.tenant import set_tenant
from costlens.k8s.service import issue_token
from costlens.models import K8sAgentTask, K8sCluster
from tests.helpers import add_to_org, make_org, sign_in_client


def setup(client: TestClient, db: Database):
    org_id, owner = make_org(db, "Acme", "owner@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        c = K8sCluster(
            id=uuid.uuid4(), org_id=org_id, name="prod", token_hash="x" * 64, token_hint="x"
        )
        token = issue_token(c)
        s.add(c)
        cid = str(c.id)
    member = add_to_org(db, org_id, "dev@acme.com", Role.MEMBER)
    return org_id, owner, member, cid, token


def agent_post(client: TestClient, token: str, path: str, body: dict):
    client.cookies.clear()
    return client.post(
        f"/api/v1/agent/{path}", json=body, headers={"Authorization": f"Bearer {token}"}
    )


LOGS = {"kind": "logs", "params": {"namespace": "shop", "pod": "api-6c-0", "previous": True}}


def test_task_rules(client: TestClient, db: Database) -> None:
    org_id, owner, member, cid, _ = setup(client, db)
    viewer = add_to_org(db, org_id, "viewer@acme.com", Role.VIEWER)
    assert (
        sign_in_client(client, db, viewer, org_id)
        .post(f"/api/v1/k8s/clusters/{cid}/tasks", json=LOGS)
        .status_code
        == 403
    )
    m = sign_in_client(client, db, member, org_id)
    url = f"/api/v1/k8s/clusters/{cid}/tasks"
    assert m.post(url, json={"kind": "exec", "params": {}}).status_code == 422
    assert (
        m.post(
            url, json={"kind": "logs", "params": {"namespace": "shop", "pod": "a;rm -rf /"}}
        ).status_code
        == 422
    )
    assert (
        m.post(
            url,
            json={"kind": "logs", "params": {"namespace": "shop", "pod": "a", "tail_lines": 9999}},
        ).status_code
        == 422
    )
    assert (
        m.post(
            url,
            json={
                "kind": "describe",
                "params": {"namespace": "shop", "kind": "Secret", "name": "db"},
            },
        ).status_code
        == 422
    )
    r = m.post(url, json={"kind": "logs", "params": {"namespace": "kube-system", "pod": "coredns"}})
    assert r.status_code == 400 and "not watched" in r.json()["detail"]
    created = m.post(url, json=LOGS)
    assert created.status_code == 202 and created.json()["status"] == "queued"
    assert created.json()["params"] == {
        "namespace": "shop",
        "pod": "api-6c-0",
        "previous": True,
        "tail_lines": 200,
    }
    for _ in range(9):
        m.post(url, json=LOGS)
    assert m.post(url, json=LOGS).status_code == 429  # queue limit
    owner_client = sign_in_client(client, db, owner, org_id)
    trail = owner_client.get(
        "/api/v1/org/audit-events", params={"action": "k8s_agent_task"}
    ).json()["items"]
    assert trail and trail[0]["changes"]["kind"] == [None, "logs"]  # who asked for what is audited


def test_claim_result_redaction_isolation_and_expiry(client: TestClient, db: Database) -> None:
    org_id, _, member, cid, token = setup(client, db)
    m = sign_in_client(client, db, member, org_id)
    tid = m.post(f"/api/v1/k8s/clusters/{cid}/tasks", json=LOGS).json()["id"]
    claimed = agent_post(client, token, "tasks/claim", {}).json()["tasks"]
    assert claimed == [{"id": tid, "kind": "logs", "params": LOGS["params"] | {"tail_lines": 200}}]
    assert (
        agent_post(client, token, "tasks/claim", {}).json()["tasks"] == []
    )  # not handed out twice
    text = "line 1\nconnect postgres://app:Hunter22@db/x\n" + "x" * 300_000
    assert agent_post(client, token, f"tasks/{tid}/result", {"ok": True, "text": text}).json() == {
        "status": "done"
    }
    assert (
        agent_post(client, token, f"tasks/{tid}/result", {"ok": True, "text": "again"}).status_code
        == 404
    )
    got = sign_in_client(client, db, member, org_id).get(f"/api/v1/k8s/tasks/{tid}").json()
    assert (
        got["status"] == "done"
        and "Hunter22" not in got["result"]
        and len(got["result"]) <= 256 * 1024
    )
    # Expiry: a task nobody claims for 10 minutes.
    t2 = m.post(f"/api/v1/k8s/clusters/{cid}/tasks", json=LOGS).json()["id"]
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        task = s.get(K8sAgentTask, uuid.UUID(t2))
        assert task is not None
        task.created_at = utcnow() - timedelta(minutes=11)
    got = sign_in_client(client, db, member, org_id).get(f"/api/v1/k8s/tasks/{t2}").json()
    assert got["status"] == "expired" and "did not pick this up" in got["error"]
    other_id, other = make_org(db, "Other", "o@other.com")
    assert (
        sign_in_client(client, db, other, other_id).get(f"/api/v1/k8s/tasks/{tid}").status_code
        == 404
    )


def test_first_scan_runs_once_after_first_full_report(client: TestClient, db: Database) -> None:
    org_id, owner, _, _, token = setup(client, db)
    pod = {
        "uid": "p1",
        "name": "api-6c-0",
        "namespace": "shop",
        "data": {
            "owners": [{"kind": "ReplicaSet", "name": "api-6c", "uid": "rs"}],
            "nodeName": "n",
            "phase": "Running",
            "containers": [{"name": "api", "image": "x"}],
            "container_statuses": [
                {
                    "name": "api",
                    "restartCount": 9,
                    "current": {"state": "waiting", "reason": "CrashLoopBackOff"},
                    "last": {"state": "terminated", "reason": "Error", "exitCode": 1},
                }
            ],
        },
    }
    healthy = {
        "uid": "p2",
        "name": "web-1",
        "namespace": "shop",
        "data": {
            "nodeName": "n",
            "phase": "Running",
            "containers": [{"name": "web", "image": "x"}],
        },
    }
    assert (
        agent_post(client, token, "events", {"events": []}).status_code == 200
    )  # nothing reported yet
    agent_post(
        client,
        token,
        "snapshot",
        {"kind": "Pod", "snapshot_id": "snap-0001", "objects": [pod, healthy]},
    )
    agent_post(client, token, "events", {"events": []})
    scan = sign_in_client(client, db, owner, org_id).get("/api/v1/clusters").json()[0]["first_scan"]
    assert scan["workloads"] == 2 and scan["failing"] == 1 and scan["pods"] == 2
    assert scan["findings"][0]["name"] == "api" and scan["findings"][0]["cause"].startswith(
        "The container keeps crashing"
    )
    at = scan["at"]
    agent_post(client, token, "events", {"events": []})
    assert (
        sign_in_client(client, db, owner, org_id)
        .get("/api/v1/clusters")
        .json()[0]["first_scan"]["at"]
        == at
    )


def test_real_agent_completes_a_logs_task(client: TestClient, db: Database) -> None:
    from costlens_agent.api import CostLensApi
    from costlens_agent.collect import Collector
    from costlens_agent.config import AgentConfig
    from costlens_agent.main import Agent
    from costlens_agent.tasks import TaskApi

    org_id, _, member, cid, token = setup(client, db)
    tid = (
        sign_in_client(client, db, member, org_id)
        .post(f"/api/v1/k8s/clusters/{cid}/tasks", json=LOGS)
        .json()["id"]
    )
    client.cookies.clear()
    api = CostLensApi(
        AgentConfig(url="http://testserver", token=token, cluster_name="prod"),
        http=cast(httpx.Client, client),
    )
    task_api = TaskApi(
        read_log=lambda name, ns, **kw: f"{name} crashed: password=Swordfish1",
        read={},
        events=lambda *a, **k: {"items": []},
        serialize=lambda x: x,
    )
    agent = Agent(api, Collector({}, serialize=lambda x: x), lambda: None, task_api=task_api)
    agent.collector.configure([], ["kube-system"])
    assert agent.tasks() == 1
    got = sign_in_client(client, db, member, org_id).get(f"/api/v1/k8s/tasks/{tid}").json()
    assert got["status"] == "done" and got["result"] == "api-6c-0 crashed: password=[REDACTED]"
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        assert "Swordfish1" not in (s.scalars(select(K8sAgentTask.result)).one() or "")
