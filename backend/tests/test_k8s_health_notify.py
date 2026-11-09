from __future__ import annotations

import json
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from costlens.accounts.roles import Role
from costlens.core import notify
from costlens.core.notify import Message, WebhookError, payload, validate_webhook
from costlens.db import Database
from costlens.db.base import utcnow
from costlens.db.tenant import set_tenant
from costlens.k8s.health import detect
from costlens.k8s.service import ingest_objects
from costlens.models import K8sCluster, K8sInvestigation, NotificationChannel
from tests.conftest import public_resolver
from tests.helpers import add_to_org, make_org, sign_in_client

SLACK = "https://hooks.slack.com/services/T000/B000/XXXXXXXXXXXXXXXX"
TEAMS = (
    "https://prod-12.westeurope.logic.azure.com/workflows/abc/triggers/manual/paths/invoke?sig=x"
)


def test_webhook_validation() -> None:
    assert validate_webhook("slack", SLACK, public_resolver) == SLACK
    assert validate_webhook("teams", TEAMS, public_resolver) == TEAMS
    assert validate_webhook("teams", "https://acme.webhook.office.com/webhookb2/x", public_resolver)
    for kind, url in [
        ("slack", "https://evil.example/hooks.slack.com/services/x"),
        ("slack", "http://hooks.slack.com/services/x"),
        ("teams", "https://hooks.slack.com/services/x"),
        ("slack", "https://hooks.slack.com.evil.example/x"),
    ]:
        with pytest.raises(WebhookError):
            validate_webhook(kind, url, public_resolver)
    with pytest.raises(WebhookError, match="not a public"):
        validate_webhook("slack", SLACK, lambda host: ["10.0.0.5"])  # DNS pointing inside


def test_payloads_are_provider_shaped_and_redacted() -> None:
    m = Message(
        "Deployment api is failing",
        "Likely cause: crash\nconn postgres://u:Secret99@db/x",
        "https://costlens.example/i/1",
    )
    slack = payload("slack", m)
    assert slack["blocks"][0]["text"]["text"] == "Deployment api is failing"
    assert slack["blocks"][-1]["elements"][0]["url"] == "https://costlens.example/i/1"
    teams = payload("teams", m)
    card = teams["attachments"][0]["content"]
    assert (
        card["type"] == "AdaptiveCard"
        and card["actions"][0]["url"] == "https://costlens.example/i/1"
    )
    assert "Secret99" not in json.dumps(slack) + json.dumps(teams)


def recorder(status: int = 200):
    sent: list[dict] = []

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append({"url": str(request.url), "body": json.loads(request.content)})
        return httpx.Response(status, text="ok")

    return sent, httpx.Client(transport=httpx.MockTransport(handle))


def test_channels_api(client: TestClient, db: Database) -> None:
    org_id, owner = make_org(db, "Acme", "owner@acme.com")
    viewer = add_to_org(db, org_id, "viewer@acme.com", Role.VIEWER)
    assert (
        sign_in_client(client, db, viewer, org_id)
        .get("/api/v1/org/notification-channels")
        .status_code
        == 403
    )
    c = sign_in_client(client, db, owner, org_id)
    assert (
        c.post(
            "/api/v1/org/notification-channels",
            json={"kind": "slack", "name": "x", "url": "https://evil.example/x"},
        ).status_code
        == 400
    )
    assert (
        c.post(
            "/api/v1/org/notification-channels",
            json={"kind": "slack", "name": "x", "url": SLACK, "events": ["nope"]},
        ).status_code
        == 422
    )
    created = c.post(
        "/api/v1/org/notification-channels", json={"kind": "slack", "name": "#oncall", "url": SLACK}
    ).json()
    assert created["url_hint"] == "…XXXXXX" and "url" not in created
    from costlens.core.notify import EVENTS

    assert created["events"] == list(EVENTS) and created["enabled"]  # every event by default
    cid = created["id"]
    sent, http = recorder()
    client.app.state.http = http  # type: ignore[attr-defined]
    assert c.post(f"/api/v1/org/notification-channels/{cid}/test").status_code == 200
    assert (
        sent[0]["url"] == SLACK
        and sent[0]["body"]["blocks"][0]["text"]["text"] == "CostLens test message"
    )
    _, failing = recorder(500)
    client.app.state.http = failing  # type: ignore[attr-defined]
    r = c.post(f"/api/v1/org/notification-channels/{cid}/test")
    assert r.status_code == 502 and "not delivered" in r.json()["detail"]
    assert "HTTPStatusError" in c.get("/api/v1/org/notification-channels").json()[0]["last_error"]
    assert (
        c.patch(f"/api/v1/org/notification-channels/{cid}", json={"enabled": False}).json()[
            "enabled"
        ]
        is False
    )
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        raw = s.scalars(select(NotificationChannel)).one()
        assert raw.url == SLACK  # decrypted transparently, stored encrypted
    assert c.delete(f"/api/v1/org/notification-channels/{cid}").status_code == 204


def crash_pod(failing: bool) -> dict:
    status = {"name": "api", "ready": not failing, "restartCount": 7 if failing else 0}
    if failing:
        status["current"] = {"state": "waiting", "reason": "CrashLoopBackOff"}
        status["last"] = {"state": "terminated", "reason": "Error", "exitCode": 1}
    return {
        "uid": "p1",
        "name": "api-6c-0",
        "namespace": "shop",
        "data": {
            "owners": [{"kind": "ReplicaSet", "name": "api-6c", "uid": "rs"}],
            "nodeName": "n",
            "phase": "Running",
            "containers": [{"name": "api", "image": "x"}],
            "container_statuses": [status],
        },
    }


def test_detect_opens_once_notifies_and_resolves(db: Database) -> None:
    sent, http = recorder()
    notify.configure(http, public_resolver)
    org_id, _ = make_org(db, "Acme", "owner@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        c = K8sCluster(
            id=uuid.uuid4(), org_id=org_id, name="prod", token_hash="x" * 64, token_hint="x"
        )
        s.add(c)
        s.add(
            NotificationChannel(
                org_id=org_id, kind="teams", name="ops", url=TEAMS, events=["k8s_incident"]
            )
        )
        s.add(
            NotificationChannel(
                org_id=org_id,
                kind="slack",
                name="off",
                url=SLACK,
                events=["k8s_incident"],
                enabled=False,
            )
        )
        s.flush()
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
                    "data": {"owners": [{"kind": "Deployment", "name": "api", "uid": "d"}]},
                }
            ],
            final=True,
        )
        ingest_objects(s, c, "Pod", "s", [crash_pod(True)], final=True)
        assert detect(s, c, utcnow(), "https://costlens.example") == {
            "opened": 1,
            "resolved": 0,
            "sent": 1,
        }
        assert detect(s, c, utcnow(), "https://costlens.example")["opened"] == 0  # no duplicates
        inv = s.scalars(select(K8sInvestigation)).one()
        assert inv.trigger == "auto" and inv.status == "open"
        assert inv.result["causes"][0]["check"] == "crash_loop"
        card = sent[0]["body"]["attachments"][0]["content"]
        assert card["body"][0]["text"] == "Deployment api is failing in prod/shop"
        assert (
            card["actions"][0]["url"]
            == f"https://costlens.example/kubernetes/investigations/{inv.id}"
        )
        assert sent[0]["url"] == TEAMS and len(sent) == 1  # disabled channel skipped
        ingest_objects(s, c, "Pod", "s2", [crash_pod(False)], final=True)
        assert detect(s, c, utcnow(), "https://costlens.example")["resolved"] == 1
        assert inv.status == "resolved" and "resolved_at" in inv.result
        assert sent[-1]["body"]["attachments"][0]["content"]["body"][0]["text"].startswith(
            "Resolved:"
        )


def test_detection_job_skips_the_demo(db: Database, settings) -> None:
    from costlens.demo.seed import ensure_demo, load_all_seeders
    from costlens.jobs.context import JobContext, configure_jobs
    from costlens.jobs.k8s import detect_health

    load_all_seeders()
    with db.session() as s:
        ensure_demo(s)
        set_tenant(s, bypass=True)
        for cl in s.scalars(select(K8sCluster)):
            cl.last_seen_at = utcnow()  # make it look live: still skipped
    configure_jobs(JobContext(settings=settings, db=db, storage=None))  # type: ignore[arg-type]
    assert detect_health() == 0
    with db.session() as s:
        set_tenant(s, bypass=True)
        assert s.scalars(select(K8sInvestigation)).all() == []
