from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from costlens.accounts.roles import Role
from costlens.db import Database
from costlens.db.base import utcnow
from costlens.db.tenant import set_tenant
from costlens.k8s.fixcheck import check
from costlens.k8s.service import ingest_events, ingest_objects
from costlens.models import K8sCluster, K8sUsage
from tests.helpers import add_to_org, make_org, sign_in_client

NOW = utcnow().replace(minute=30, second=0, microsecond=0)


def pod(failing: bool) -> dict:
    st = {"name": "api", "restartCount": 3}
    if failing:
        st["current"] = {"state": "waiting", "reason": "CrashLoopBackOff"}
    return {
        "uid": "p1",
        "name": "api-6c-0",
        "namespace": "shop",
        "data": {
            "owners": [{"kind": "ReplicaSet", "name": "api-6c", "uid": "rs"}],
            "nodeName": "n",
            "phase": "Running",
            "containers": [{"name": "api", "image": "x"}],
            "container_statuses": [st],
        },
    }


def backoff(uid: str, hours_ago: float, count: int) -> dict:
    at = NOW - timedelta(hours=hours_ago)
    return {
        "uid": uid,
        "namespace": "shop",
        "object_kind": "Pod",
        "object_name": "api-6c-0",
        "type": "Warning",
        "reason": "BackOff",
        "message": "Back-off restarting failed container",
        "count": count,
        "first_at": at - timedelta(minutes=30),
        "last_at": at,
    }


def run(
    db: Database,
    events: list[dict],
    failing_now: bool,
    fix_hours_ago: float,
    mem_after: float = 1.0,
):
    org_id, _ = make_org(db, f"Acme {uuid.uuid4().hex[:6]}", f"{uuid.uuid4().hex[:6]}@x.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        c = K8sCluster(
            id=uuid.uuid4(), org_id=org_id, name="prod", token_hash="x" * 64, token_hint="x"
        )
        s.add(c)
        s.flush()
        ingest_objects(s, c, "Pod", "s", [pod(failing_now)], final=True)
        ingest_events(s, c, events)
        for h in range(30):
            hour = (NOW - timedelta(hours=h)).replace(minute=0)
            mem = mem_after if h < fix_hours_ago else 1.0
            s.add(
                K8sUsage(
                    org_id=org_id,
                    cluster_id=c.id,
                    hour=hour,
                    namespace="shop",
                    kind="Deployment",
                    name="api",
                    container="api",
                    samples=60,
                    cpu_sum=30,
                    cpu_max=0.6,
                    memory_sum=mem * 60,
                    memory_max=mem,
                    cpu_request=1,
                    memory_request=2,
                    replicas=1,
                )
            )
        s.flush()
        return check(
            s,
            c,
            namespace="shop",
            kind="Deployment",
            name="api",
            fix_at=NOW - timedelta(hours=fix_hours_ago),
            now=NOW,
        )


@pytest.mark.parametrize(
    ("events", "failing", "fix_ago", "verdict"),
    [
        ([backoff("a", 4, 20), backoff("b", 3.2, 20)], False, 3, "fixed"),
        ([backoff("a", 4, 20), backoff("b", 3.2, 20), backoff("c", 1, 4)], False, 3, "improving"),
        ([backoff("a", 4, 20), backoff("b", 1, 40)], True, 3, "not_fixed"),
        ([backoff("a", 2, 20)], True, 0.3, "too_early"),
    ],
)
def test_verdicts(db: Database, events, failing, fix_ago, verdict) -> None:
    r = run(db, events, failing, fix_ago)
    assert r["verdict"] == verdict, r["text"]
    before, during, after = r["periods"]
    assert (before["period"], during["period"], after["period"]) == ("before", "during", "after")
    if verdict == "fixed":
        assert after["failures"] == 0 and during["failures"] == 40
        assert r["text"].startswith("Fixed: no failures in the 3 hours since the fix")
    if verdict == "too_early":
        assert "check again after" in r["text"]


def test_side_effects_are_reported(db: Database) -> None:
    r = run(db, [backoff("a", 4, 20)], False, 3, mem_after=1.6)
    assert r["verdict"] == "fixed"
    assert r["side_effects"] == ["memory per replica is 60% higher than before the incident"]


def test_api_with_change_and_saved_on_investigation(client: TestClient, db: Database) -> None:
    from costlens.demo.seed import ensure_demo, load_all_seeders

    load_all_seeders()
    with db.session() as s:
        demo_id = ensure_demo(s).id
    _, visitor = make_org(db, "Visitor", "visitor@x.com")
    add_to_org(db, demo_id, "visitor@x.com", Role.VIEWER)
    v = sign_in_client(client, db, visitor, demo_id)
    cid = v.get("/api/v1/clusters").json()[0]["id"]
    deploy = next(c for c in v.get("/api/v1/changes").json() if "nightly-etl" in c["title"])
    body = {
        "cluster_id": cid,
        "namespace": "data",
        "kind": "CronJob",
        "name": "nightly-etl",
        "change_id": deploy["id"],
    }
    r = v.post("/api/v1/k8s/fix-check", json=body).json()
    assert r["verdict"] == "not_fixed" and r["health_now"] == "failing"  # the deploy made it worse
    assert v.post("/api/v1/k8s/fix-check", json={**body, "change_id": None}).status_code == 400
    # Saved on an investigation (own organisation, member).
    org_id, _ = make_org(db, "Acme", "owner@acme.com")
    member = add_to_org(db, org_id, "dev@acme.com", Role.MEMBER)
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        c = K8sCluster(
            id=uuid.uuid4(), org_id=org_id, name="prod", token_hash="x" * 64, token_hint="x"
        )
        s.add(c)
        s.flush()
        ingest_objects(s, c, "Pod", "s", [pod(False)], final=True)
        own_cid = str(c.id)
    m = sign_in_client(client, db, member, org_id)
    inv = m.post(
        "/api/v1/k8s/investigations",
        json={"cluster_id": own_cid, "namespace": "shop", "kind": "Deployment", "name": "api"},
    ).json()
    saved = m.post(
        f"/api/v1/k8s/investigations/{inv['id']}/fix-check",
        json={"fix_at": (utcnow() - timedelta(hours=2)).isoformat()},
    )
    assert saved.status_code == 200 and saved.json()["verdict"] == "fixed"
    assert (
        m.get(f"/api/v1/k8s/investigations/{inv['id']}").json()["result"]["fix_check"]["verdict"]
        == "fixed"
    )
    ns_inv = m.post(
        "/api/v1/k8s/investigations", json={"cluster_id": own_cid, "namespace": "shop"}
    ).json()
    assert (
        m.post(
            f"/api/v1/k8s/investigations/{ns_inv['id']}/fix-check",
            json={"fix_at": utcnow().isoformat()},
        ).status_code
        == 400
    )
