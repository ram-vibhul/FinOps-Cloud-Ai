from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from ccradar.accounts.roles import Role
from ccradar.db import Database
from ccradar.db.base import utcnow
from ccradar.db.tenant import set_tenant
from ccradar.models import K8sCluster, K8sInvestigation
from tests.helpers import add_to_org, make_org, sign_in_client


def make_inv(s, org_id, cluster_id, ranked: list[str]) -> str:
    now = utcnow()
    inv = K8sInvestigation(
        org_id=org_id,
        cluster_id=cluster_id,
        namespace="shop",
        kind="Deployment",
        name="api",
        window_start=now,
        window_end=now,
        title="t",
        trigger="manual",
        result={"causes": [{"check": c} for c in ranked]},
    )
    s.add(inv)
    s.flush()
    return str(inv.id)


@pytest.fixture
def setup(db: Database):
    org_id, owner = make_org(db, "Acme", "owner@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        c = K8sCluster(
            id=uuid.uuid4(), org_id=org_id, name="prod", token_hash="x" * 64, token_hint="x"
        )
        s.add(c)
        s.flush()
        ids = [
            make_inv(s, org_id, c.id, r)
            for r in (
                ["oom", "crash_loop"],  # real oom -> right (rank 1)
                ["crash_loop", "oom"],  # real oom -> wrong first, rank 2
                ["crash_loop"],  # real crash_loop -> right
                ["probes", "cpu_throttling"],  # real dns -> missed entirely
            )
        ]
    member = add_to_org(db, org_id, "dev@acme.com", Role.MEMBER)
    viewer = add_to_org(db, org_id, "viewer@acme.com", Role.VIEWER)
    return org_id, owner, member, viewer, ids


def test_close_rules(client: TestClient, db: Database, setup) -> None:
    org_id, owner, member, viewer, ids = setup
    v = sign_in_client(client, db, viewer, org_id)
    assert (
        v.post(f"/api/v1/k8s/investigations/{ids[0]}/close", json={"real_cause": "oom"}).status_code
        == 403
    )
    m = sign_in_client(client, db, member, org_id)
    assert (
        m.post(
            f"/api/v1/k8s/investigations/{ids[0]}/close", json={"real_cause": "aliens"}
        ).status_code
        == 422
    )
    r = m.post(
        f"/api/v1/k8s/investigations/{ids[1]}/close",
        json={
            "real_cause": "oom",
            "cause_note": "batch size doubled",
            "fix_note": "limit 512Mi -> 1Gi",
        },
    ).json()
    assert r["status"] == "closed" and r["top_cause"] == "crash_loop" and r["cause_rank"] == 2
    assert r["fix_note"] == "limit 512Mi -> 1Gi" and r["closed_at"]
    trail = (
        sign_in_client(client, db, owner, org_id)
        .get("/api/v1/org/audit-events", params={"action": "k8s_investigation"})
        .json()["items"]
    )
    assert any(e["changes"].get("real_cause") == [None, "oom"] for e in trail)


def test_accuracy_numbers(client: TestClient, db: Database, setup) -> None:
    org_id, _, member, _, ids = setup
    m = sign_in_client(client, db, member, org_id)
    empty = m.get("/api/v1/k8s/accuracy").json()
    assert empty["closed"] == 0 and empty["top1_pct"] is None and empty["lab"]["accuracy"] == 1.0
    for inv_id, real in zip(ids, ("oom", "oom", "crash_loop", "dns"), strict=True):
        m.post(f"/api/v1/k8s/investigations/{inv_id}/close", json={"real_cause": real})
    a = m.get("/api/v1/k8s/accuracy").json()
    assert a["closed"] == 4 and a["top1_pct"] == 50.0 and a["top3_pct"] == 75.0 and a["missed"] == 1
    by = {c["check"]: c for c in a["checks"]}
    assert by["oom"]["recall_pct"] == 50.0 and by["oom"]["precision_pct"] == 100.0
    assert by["crash_loop"]["precision_pct"] == 50.0 and by["crash_loop"]["recall_pct"] == 100.0
    assert by["dns"]["recall_pct"] == 0.0 and by["probes"]["precision_pct"] == 0.0
    assert a["by_month"][0]["closed"] == 4 and a["by_month"][0]["top1_pct"] == 50.0
    assert {"check": "other", "label": "Something else"} in a["causes"]
    other_id, other = make_org(db, "Other", "o@other.com")
    assert (
        sign_in_client(client, db, other, other_id).get("/api/v1/k8s/accuracy").json()["closed"]
        == 0
    )
