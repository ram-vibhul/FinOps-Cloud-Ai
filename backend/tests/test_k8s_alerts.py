from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ccradar.accounts.roles import Role
from ccradar.db import Database
from ccradar.db.base import utcnow
from ccradar.db.tenant import set_tenant
from ccradar.k8s import alerts
from ccradar.k8s.service import ingest_objects
from ccradar.models import K8sCluster, K8sInvestigation
from tests.helpers import add_to_org, make_org, sign_in_client


def crash_pod() -> dict:
    return {
        "uid": "p1",
        "name": "checkout-7f9-x2",
        "namespace": "shop",
        "data": {
            "owners": [{"kind": "ReplicaSet", "name": "checkout-7f9", "uid": "rs"}],
            "nodeName": "n",
            "phase": "Running",
            "containers": [{"name": "app", "image": "x"}],
            "container_statuses": [
                {
                    "name": "app",
                    "restartCount": 6,
                    "current": {"state": "waiting", "reason": "CrashLoopBackOff"},
                    "last": {"state": "terminated", "reason": "Error", "exitCode": 1},
                }
            ],
        },
    }


def setup(db: Database):
    org_id, owner = make_org(db, "Acme", "owner@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        c = K8sCluster(
            id=uuid.uuid4(), org_id=org_id, name="prod", token_hash="x" * 64, token_hint="x"
        )
        s.add(c)
        s.flush()
        ingest_objects(
            s,
            c,
            "ReplicaSet",
            "s",
            [
                {
                    "uid": "rs",
                    "name": "checkout-7f9",
                    "namespace": "shop",
                    "data": {"owners": [{"kind": "Deployment", "name": "checkout", "uid": "d"}]},
                }
            ],
            final=True,
        )
        ingest_objects(s, c, "Pod", "s", [crash_pod()], final=True)
        cid = c.id
    return org_id, owner, cid


ALERTMANAGER = {
    "version": "4",
    "status": "firing",
    "receiver": "ccradar",
    "alerts": [
        {
            "status": "firing",
            "fingerprint": "a1b2c3d4e5f60718",
            "labels": {
                "alertname": "KubePodCrashLooping",
                "namespace": "shop",
                "pod": "checkout-7f9-x2",
                "container": "app",
                "severity": "warning",
            },
            "annotations": {"summary": "Pod shop/checkout-7f9-x2 is crash looping."},
            "startsAt": "2026-11-05T09:00:00Z",
        }
    ],
}


def test_verify_signatures() -> None:
    body = b'{"event":{}}'
    good = "v1=" + hmac.new(b"pd-secret", body, hashlib.sha256).hexdigest()
    assert alerts.verify("pagerduty", "pd-secret", body, {"x-pagerduty-signature": good})
    assert alerts.verify(
        "pagerduty", "pd-secret", body, {"x-pagerduty-signature": f"v1=old, {good}"}
    )
    assert not alerts.verify(
        "pagerduty", "pd-secret", body, {"x-pagerduty-signature": "v1=deadbeef"}
    )
    assert not alerts.verify("pagerduty", "pd-secret", body, {})
    assert alerts.verify("alertmanager", "tok", body, {"authorization": "Bearer tok"})
    assert alerts.verify("opsgenie", "tok", body, {"x-cloudcostradar-token": "tok"})
    assert not alerts.verify("alertmanager", "tok", body, {"authorization": "Bearer nope"})
    assert not alerts.verify("alertmanager", "tok", body, {})


def test_parsers_and_targets(db: Database) -> None:
    am = alerts.parse("alertmanager", ALERTMANAGER)
    assert am[0].fingerprint == "alertmanager:a1b2c3d4e5f60718" and am[0].firing
    assert am[0].title == "Pod shop/checkout-7f9-x2 is crash looping."
    pd = alerts.parse(
        "pagerduty",
        {
            "event": {
                "event_type": "incident.triggered",
                "occurred_at": "2026-11-05T09:00:00Z",
                "data": {
                    "id": "Q1W2E3",
                    "title": "High error rate namespace=shop deployment=checkout",
                    "service": {"summary": "Checkout"},
                },
            }
        },
    )
    assert pd[0].fingerprint == "pagerduty:Q1W2E3" and pd[0].labels == {
        "namespace": "shop",
        "deployment": "checkout",
    }
    assert (
        alerts.parse(
            "pagerduty", {"event": {"event_type": "incident.resolved", "data": {"id": "Q1W2E3"}}}
        )[0].firing
        is False
    )
    assert alerts.parse("pagerduty", {"event": {"event_type": "pagey.ping"}}) == []
    og = alerts.parse(
        "opsgenie",
        {
            "action": "Create",
            "alert": {
                "alertId": "og-1",
                "message": "Checkout down",
                "tags": ["team:payments", "namespace:shop"],
                "details": {"deployment": "checkout"},
            },
        },
    )
    assert og[0].labels["namespace"] == "shop" and og[0].labels["deployment"] == "checkout"
    assert (
        alerts.parse("opsgenie", {"action": "Close", "alert": {"alertId": "og-1"}})[0].firing
        is False
    )
    org_id, _, cid = setup(db)
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        cluster = s.get(K8sCluster, cid)
        assert cluster is not None
        assert alerts.target(s, cluster, am[0].labels) == (
            "shop",
            "Deployment",
            "checkout",
        )  # pod -> owner
        assert alerts.target(s, cluster, {"namespace": "shop", "statefulset": "db"}) == (
            "shop",
            "StatefulSet",
            "db",
        )
        assert alerts.target(s, cluster, {"alertname": "x"}) == (None, None, None)


def test_alertmanager_flow_dedup_and_resolve(client: TestClient, db: Database) -> None:
    org_id, owner, cid = setup(db)
    c = sign_in_client(client, db, owner, org_id)
    created = c.post(
        "/api/v1/org/webhooks/alerts",
        json={"kind": "alertmanager", "name": "prod AM", "cluster_id": str(cid)},
    ).json()
    assert created["url"].endswith(f"/api/v1/hooks/alertmanager/{created['id']}")
    assert "send_resolved: true" in created["setup"] and created["secret"] in created["setup"]
    path = "/api/v1/hooks/alertmanager/" + created["id"]
    auth = {"Authorization": f"Bearer {created['secret']}"}
    client.cookies.clear()
    assert (
        client.post(path, json=ALERTMANAGER, headers={"Authorization": "Bearer wrong"}).status_code
        == 401
    )
    assert (
        client.post(f"/api/v1/hooks/pagerduty/{created['id']}", json={}, headers=auth).status_code
        == 401
    )
    first = client.post(path, json=ALERTMANAGER, headers=auth).json()
    assert first == {"status": "accepted", "opened": 1, "refreshed": 0, "resolved": 0}
    assert (
        client.post(path, json=ALERTMANAGER, headers=auth).json()["refreshed"] == 1
    )  # same fingerprint
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        inv = s.scalars(select(K8sInvestigation)).one()
        assert inv.trigger == "alert" and (inv.kind, inv.name) == ("Deployment", "checkout")
        assert inv.result["alert"]["source"] == "alertmanager"
        assert inv.result["causes"][0]["check"] == "crash_loop"
        assert inv.title.startswith("[alertmanager] Pod shop/checkout-7f9-x2 is crash looping.")
    resolved = {**ALERTMANAGER, "alerts": [{**ALERTMANAGER["alerts"][0], "status": "resolved"}]}
    assert client.post(path, json=resolved, headers=auth).json()["resolved"] == 1
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        assert s.scalars(select(K8sInvestigation)).one().status == "resolved"


def test_pagerduty_uses_its_own_signing_secret(client: TestClient, db: Database) -> None:
    org_id, owner, cid = setup(db)
    c = sign_in_client(client, db, owner, org_id)
    hook = c.post(
        "/api/v1/org/webhooks/alerts",
        json={"kind": "pagerduty", "name": "PD", "cluster_id": str(cid)},
    ).json()
    assert "Set signing secret" in hook["setup"]
    assert (
        c.put(
            f"/api/v1/org/webhooks/{hook['id']}/secret", json={"secret": "pd-generated-secret"}
        ).status_code
        == 204
    )
    body = json.dumps(
        {
            "event": {
                "event_type": "incident.triggered",
                "occurred_at": utcnow().isoformat(),
                "data": {"id": "PD9", "title": "Errors namespace=shop deployment=checkout"},
            }
        }
    ).encode()
    sig = "v1=" + hmac.new(b"pd-generated-secret", body, hashlib.sha256).hexdigest()
    client.cookies.clear()
    r = client.post(
        f"/api/v1/hooks/pagerduty/{hook['id']}",
        content=body,
        headers={"X-PagerDuty-Signature": sig, "Content-Type": "application/json"},
    )
    assert r.json()["opened"] == 1


def test_detection_does_not_duplicate_alert_investigations(
    client: TestClient, db: Database
) -> None:
    from ccradar.k8s.health import detect

    org_id, owner, cid = setup(db)
    c = sign_in_client(client, db, owner, org_id)
    hook = c.post(
        "/api/v1/org/webhooks/alerts",
        json={"kind": "alertmanager", "name": "AM", "cluster_id": str(cid)},
    ).json()
    client.cookies.clear()
    client.post(
        f"/api/v1/hooks/alertmanager/{hook['id']}",
        json=ALERTMANAGER,
        headers={"Authorization": f"Bearer {hook['secret']}"},
    )
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        cluster = s.get(K8sCluster, cid)
        assert cluster is not None
        assert detect(s, cluster, utcnow(), "https://x")["opened"] == 0
        assert len(s.scalars(select(K8sInvestigation)).all()) == 1


@pytest.mark.parametrize(
    ("window", "status"),
    [
        ({"start": -5, "end": -1}, 200),
        ({"start": -1, "end": -5}, 422),
        ({"start": -100, "end": -1}, 422),
        ({"start": 2, "end": 5}, 400),
        ({"start": -5}, 422),
    ],
)
def test_custom_window(client: TestClient, db: Database, window: dict, status: int) -> None:
    org_id, _, cid = setup(db)
    member = add_to_org(db, org_id, "dev@acme.com", Role.MEMBER)
    m = sign_in_client(client, db, member, org_id)
    now = utcnow()
    body = {
        "cluster_id": str(cid),
        "namespace": "shop",
        "kind": "Deployment",
        "name": "checkout",
        **{k: (now + timedelta(hours=v)).isoformat() for k, v in window.items()},
    }
    r = m.post("/api/v1/k8s/investigations/run", json=body)
    assert r.status_code == status, r.text
    if status == 200:
        assert r.json()["window_end"] <= now.isoformat()
