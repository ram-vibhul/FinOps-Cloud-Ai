from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from ccradar.accounts.roles import Role
from ccradar.db import Database
from ccradar.db.base import utcnow
from ccradar.db.tenant import set_tenant
from ccradar.models import AuditEvent, K8sCluster, K8sObject
from tests.helpers import add_to_org, make_org, sign_in_client


def connect(client: TestClient, db: Database, name: str = "prod-eu"):
    org_id, owner = make_org(db, "Acme", "owner@acme.com")
    c = sign_in_client(client, db, owner, org_id)
    r = c.post("/api/v1/clusters", json={"name": name})
    assert r.status_code == 201, r.text
    return org_id, owner, c, r.json()


def agent(client: TestClient, token: str):
    class Agent:
        def post(self, path: str, body: dict):
            client.cookies.clear()
            return client.post(
                f"/api/v1/agent/{path}", json=body, headers={"Authorization": f"Bearer {token}"}
            )

    return Agent()


def pod(uid: str, name: str, ns: str = "shop") -> dict:
    return {"uid": uid, "name": name, "namespace": ns, "data": {"phase": "Running"}}


def test_create_returns_token_once_and_install_command(client: TestClient, db: Database) -> None:
    org_id, _, c, created = connect(client, db)
    assert created["token"].startswith("ckt_") and created["status"] == "waiting"
    assert "helm upgrade --install cloudcostradar-agent" in created["install_command"]
    assert created["token"] in created["install_command"]
    listed = c.get("/api/v1/clusters").json()
    assert listed[0]["name"] == "prod-eu" and "token" not in listed[0]
    assert listed[0]["health"] == "waiting"
    assert c.post("/api/v1/clusters", json={"name": "prod-eu"}).status_code == 409
    assert c.post("/api/v1/clusters", json={"name": "Prod EU"}).status_code == 422
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        cluster = s.scalars(select(K8sCluster)).one()
        # Only a digest is kept (the secret may contain "_": take all after the prefix).
        secret = created["token"].split("_", 2)[2]
        assert len(secret) > 20 and secret not in cluster.token_hash


def test_agent_hello_snapshot_and_deletions(client: TestClient, db: Database) -> None:
    org_id, owner, _, created = connect(client, db)
    a = agent(client, created["token"])
    config = a.post(
        "hello", {"agent_version": "0.1.0", "kubernetes_version": "v1.33.2", "provider": "eks"}
    ).json()
    assert "kube-system" in config["exclude_namespaces"]
    first = a.post(
        "snapshot",
        {
            "kind": "Pod",
            "snapshot_id": "snap-0001",
            "final": False,
            "objects": [pod("u1", "web-1"), pod("u2", "web-2")],
        },
    )
    assert first.json() == {"received": 2, "created": 2, "deleted": 0}
    assert (
        a.post(
            "snapshot",
            {
                "kind": "Pod",
                "snapshot_id": "snap-0001",
                "final": True,
                "objects": [pod("u3", "worker-1")],
            },
        ).json()["deleted"]
        == 0
    )
    # Next snapshot: web-2 is gone.
    r = a.post(
        "snapshot",
        {
            "kind": "Pod",
            "snapshot_id": "snap-0002",
            "final": True,
            "objects": [pod("u1", "web-1"), pod("u3", "worker-1")],
        },
    )
    assert r.json() == {"received": 2, "created": 0, "deleted": 1}
    a.post(
        "snapshot",
        {
            "kind": "Node",
            "snapshot_id": "snap-0002",
            "final": True,
            "objects": [{"uid": "n1", "name": "ip-10-0-0-1", "data": {}}],
        },
    )
    c = sign_in_client(client, db, owner, org_id)
    listed = c.get("/api/v1/clusters").json()[0]
    assert listed["health"] == "ok" and listed["status"] == "connected"
    assert listed["objects"] == {"Pod": 2, "Node": 1} and listed["node_count"] == 1
    assert listed["kubernetes_version"] == "v1.33.2"
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        gone = s.scalars(select(K8sObject).where(K8sObject.uid == "u2")).one()
        assert gone.deleted_at is not None
        # Heartbeats are not audit noise: only the creation was recorded.
        actions = [e.action for e in s.scalars(select(AuditEvent))]
        assert actions.count("k8s_cluster.created") == 1
        assert "k8s_cluster.updated" not in actions


def test_events_upsert(client: TestClient, db: Database) -> None:
    _, _, _, created = connect(client, db)
    a = agent(client, created["token"])
    now = utcnow()
    event = {
        "uid": "e1",
        "namespace": "shop",
        "object_kind": "Pod",
        "object_name": "web-1",
        "type": "Warning",
        "reason": "BackOff",
        "message": "Back-off restarting failed container",
        "count": 3,
        "first_at": (now - timedelta(minutes=5)).isoformat(),
        "last_at": now.isoformat(),
    }
    assert a.post("events", {"events": [event]}).json() == {"received": 1}
    assert a.post("events", {"events": [{**event, "count": 7}]}).json() == {"received": 1}
    from ccradar.models import K8sEvent

    with db.session() as s:
        set_tenant(s, bypass=True)
        rows = s.scalars(select(K8sEvent)).all()
        assert len(rows) == 1 and rows[0].count == 7


def test_bad_tokens_rotation_kinds_and_isolation(client: TestClient, db: Database) -> None:
    org_id, owner, c, created = connect(client, db)
    token = created["token"]
    cid = created["id"]
    for bad in ("", "nope", "ckt_zz_abc", token[:-2] + "xx", f"ckt_{'0' * 32}_abc"):
        r = agent(client, bad).post("hello", {"agent_version": "0.1.0"})
        assert r.status_code == 401, bad
    assert (
        agent(client, token)
        .post("snapshot", {"kind": "Secret", "snapshot_id": "snap-0001", "objects": []})
        .status_code
        == 422
    )
    c = sign_in_client(client, db, owner, org_id)
    new = c.post(f"/api/v1/clusters/{cid}/rotate-token").json()["token"]
    assert agent(client, token).post("hello", {"agent_version": "0.1.0"}).status_code == 401
    assert agent(client, new).post("hello", {"agent_version": "0.0.9"}).status_code == 200
    assert (
        sign_in_client(client, db, owner, org_id).get("/api/v1/clusters").json()[0]["health"]
        == "outdated"
    )
    # Viewers see clusters but cannot add, rotate or delete; other orgs see nothing.
    viewer = add_to_org(db, org_id, "viewer@acme.com", Role.VIEWER)
    v = sign_in_client(client, db, viewer, org_id)
    assert len(v.get("/api/v1/clusters").json()) == 1
    assert v.post("/api/v1/clusters", json={"name": "x"}).status_code == 403
    assert v.delete(f"/api/v1/clusters/{cid}").status_code == 403
    other_id, other = make_org(db, "Other", "o@other.com")
    o = sign_in_client(client, db, other, other_id)
    assert o.get("/api/v1/clusters").json() == []
    assert o.delete(f"/api/v1/clusters/{cid}").status_code == 404
    c = sign_in_client(client, db, owner, org_id)
    assert c.delete(f"/api/v1/clusters/{cid}").status_code == 204
    assert agent(client, new).post("hello", {"agent_version": "0.1.0"}).status_code == 401


def test_silent_cluster(client: TestClient, db: Database) -> None:
    org_id, owner, _, _ = connect(client, db)
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        cluster = s.scalars(select(K8sCluster)).one()
        cluster.last_seen_at = utcnow() - timedelta(hours=2)
        cluster.agent_version = "0.1.0"
    c = sign_in_client(client, db, owner, org_id)
    assert c.get("/api/v1/clusters").json()[0]["health"] == "silent"


def test_retention_purges_old_events_and_removed_objects_only(
    client: TestClient, db: Database
) -> None:
    from ccradar.models import K8sEvent
    from ccradar.privacy.retention import apply_retention

    org_id, _, _, created = connect(client, db)
    a = agent(client, created["token"])
    old = utcnow() - timedelta(days=60)
    a.post(
        "events",
        {
            "events": [
                {
                    "uid": "old",
                    "reason": "BackOff",
                    "first_at": old.isoformat(),
                    "last_at": old.isoformat(),
                },
                {
                    "uid": "new",
                    "reason": "BackOff",
                    "first_at": utcnow().isoformat(),
                    "last_at": utcnow().isoformat(),
                },
            ]
        },
    )
    a.post(
        "snapshot",
        {"kind": "Pod", "snapshot_id": "snap-0001", "objects": [pod("u1", "a"), pod("u2", "b")]},
    )
    a.post("snapshot", {"kind": "Pod", "snapshot_id": "snap-0002", "objects": [pod("u1", "a")]})
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        removed = s.scalars(select(K8sObject).where(K8sObject.uid == "u2")).one()
        removed.deleted_at = old
        live = s.scalars(select(K8sObject).where(K8sObject.uid == "u1")).one()
        live.first_seen_at = old  # old but still running: kept
    with db.session() as s:
        totals = apply_retention(s)
    assert totals["k8s_events"] == 1 and totals["k8s_removed_objects"] == 1
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        assert [e.uid for e in s.scalars(select(K8sEvent))] == ["new"]
        assert [o.uid for o in s.scalars(select(K8sObject))] == ["u1"]


def test_real_agent_end_to_end(client: TestClient, db: Database) -> None:
    """The agent's own code (fake Kubernetes API) reporting into the real app."""
    from types import SimpleNamespace
    from typing import cast

    import httpx
    from ccradar_agent.api import CloudCostRadarApi
    from ccradar_agent.collect import Collector
    from ccradar_agent.config import AgentConfig
    from ccradar_agent.main import SNAPSHOT_KINDS, Agent

    org_id, owner, _, created = connect(client, db)
    client.cookies.clear()

    def page(items):
        return SimpleNamespace(items=items, metadata=SimpleNamespace(_continue=None))

    pods = [
        {
            "metadata": {"uid": f"p{i}", "name": f"web-{i}", "namespace": ns},
            "spec": {
                "containers": [
                    {
                        "name": "web",
                        "image": "web:1",
                        "env": [{"name": "PASSWORD", "value": "hunter2"}],
                    }
                ]
            },
            "status": {"phase": "Running"},
        }
        for i, ns in enumerate(["shop", "shop", "kube-system"])
    ]
    listers = {kind: (lambda **_: page([])) for kind in SNAPSHOT_KINDS}
    listers["Pod"] = lambda **_: page(pods)
    listers["Node"] = lambda **_: page(
        [
            {
                "metadata": {"uid": "n1", "name": "node-1"},
                "spec": {"providerID": "aws:///eu-west-1a/i-1"},
            }
        ]
    )
    listers["Event"] = lambda **_: page(
        [
            {
                "metadata": {"uid": "e1", "namespace": "shop"},
                "type": "Warning",
                "reason": "BackOff",
                "message": "Back-off restarting failed container",
                "count": 2,
                "involvedObject": {"kind": "Pod", "name": "web-0"},
                "lastTimestamp": utcnow().isoformat(),
            }
        ]
    )
    config = AgentConfig(url="http://testserver", token=created["token"], cluster_name="prod-eu")
    agent = Agent(
        CloudCostRadarApi(config, http=cast("httpx.Client", client)),
        Collector(listers, lambda x: x),
        lambda: "v1.33.2",
    )
    agent.tick()
    c = sign_in_client(client, db, owner, org_id)
    listed = c.get("/api/v1/clusters").json()[0]
    assert listed["provider"] == "eks" and listed["health"] == "ok"
    assert listed["objects"] == {"Pod": 2, "Node": 1}  # kube-system skipped by default
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        assert "hunter2" not in str([o.data for o in s.scalars(select(K8sObject))])


def test_watched_namespaces_reach_the_agent(client: TestClient, db: Database) -> None:
    org_id, owner, c, created = connect(client, db)
    a = agent(client, created["token"])
    a.post(
        "snapshot",
        {
            "kind": "Namespace",
            "snapshot_id": "snap-0001",
            "objects": [
                {"uid": f"ns-{n}", "name": n, "data": {}}
                for n in ("shop", "billing", "kube-system")
            ],
        },
    )
    c = sign_in_client(client, db, owner, org_id)
    cid = created["id"]
    listed = c.get("/api/v1/clusters").json()[0]
    assert listed["namespaces"] == ["billing", "kube-system", "shop"]
    assert sorted(listed["exclude_namespaces"]) == ["kube-node-lease", "kube-public", "kube-system"]
    assert (
        c.patch(f"/api/v1/clusters/{cid}", json={"include_namespaces": ["Shop!"]}).status_code
        == 422
    )
    r = c.patch(
        f"/api/v1/clusters/{cid}", json={"include_namespaces": ["shop", "shop", "billing"]}
    ).json()
    assert r["include_namespaces"] == ["billing", "shop"]
    config = agent(client, created["token"]).post("hello", {"agent_version": "0.1.0"}).json()
    assert config["include_namespaces"] == ["billing", "shop"]
    c = sign_in_client(client, db, owner, org_id)
    assert c.patch(f"/api/v1/clusters/{cid}", json={"name": "prod-eu"}).status_code == 200


def test_revoke_then_new_token(client: TestClient, db: Database) -> None:
    org_id, owner, c, created = connect(client, db)
    assert (
        agent(client, created["token"]).post("hello", {"agent_version": "0.1.0"}).status_code == 200
    )
    c = sign_in_client(client, db, owner, org_id)
    revoked = c.post(f"/api/v1/clusters/{created['id']}/revoke").json()
    assert revoked["health"] == "revoked" and revoked["status"] == "revoked"
    assert (
        agent(client, created["token"]).post("hello", {"agent_version": "0.1.0"}).status_code == 401
    )
    c = sign_in_client(client, db, owner, org_id)
    fresh = c.post(f"/api/v1/clusters/{created['id']}/rotate-token").json()
    assert fresh["status"] == "waiting"
    assert (
        agent(client, fresh["token"]).post("hello", {"agent_version": "0.1.0"}).status_code == 200
    )


def test_silent_agent_warned_once_and_back(db: Database) -> None:
    import json as _json

    import httpx

    from ccradar.core import notify
    from ccradar.k8s.health import check_agents
    from ccradar.models import NotificationChannel
    from tests.conftest import public_resolver

    sent: list[dict] = []
    notify.configure(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda r: sent.append(_json.loads(r.content)) or httpx.Response(200)
            )
        ),
        public_resolver,
    )
    org_id, _ = make_org(db, "Acme", "owner@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        s.add(
            NotificationChannel(
                org_id=org_id,
                kind="slack",
                name="ops",
                events=["k8s_incident"],
                url="https://hooks.slack.com/services/T/B/X",
            )
        )
        cl = K8sCluster(
            id=uuid.uuid4(),
            org_id=org_id,
            name="prod",
            token_hash="x" * 64,
            token_hint="x",
            last_seen_at=utcnow() - timedelta(hours=1),
        )
        s.add(cl)
        s.flush()
        assert check_agents(s, utcnow(), "https://x") == {"silent": 1, "back": 0}
        assert check_agents(s, utcnow(), "https://x") == {"silent": 0, "back": 0}  # once
        cl.last_seen_at = utcnow()
        assert check_agents(s, utcnow(), "https://x") == {"silent": 0, "back": 1}
    assert [m["blocks"][0]["text"]["text"] for m in sent] == [
        "CloudCostRadar agent in prod stopped reporting",
        "CloudCostRadar agent in prod is reporting again",
    ]
