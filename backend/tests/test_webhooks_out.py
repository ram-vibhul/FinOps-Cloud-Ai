from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from costlens.core import webhooks_out
from costlens.core.notify import Message, notify
from costlens.db import Database
from costlens.db.tenant import set_tenant
from costlens.models import WebhookDelivery
from tests.conftest import public_resolver
from tests.helpers import make_org, sign_in_client


class Receiver:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.answers: list[int] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        code = self.answers.pop(0) if self.answers else 200
        return httpx.Response(code, headers={"Location": "https://elsewhere.example"})


def test_sign_and_verify() -> None:
    body = b'{"a":1}'
    header = webhooks_out.sign("whsec_x", body, 1_000_000)
    assert header.startswith("t=1000000,v1=")
    assert webhooks_out.verify("whsec_x", body, header, now=1_000_100)
    assert not webhooks_out.verify("whsec_y", body, header, now=1_000_100)  # wrong secret
    assert not webhooks_out.verify("whsec_x", b'{"a":2}', header, now=1_000_100)  # changed body
    assert not webhooks_out.verify("whsec_x", body, header, now=1_000_400)  # too old
    assert not webhooks_out.verify("whsec_x", body, "garbage", now=1_000_000)


def test_events_are_signed_retried_and_marked_failed(  # noqa: PLR0915 - one end-to-end story
    client: TestClient, db: Database
) -> None:
    receiver = Receiver()
    http = httpx.Client(transport=httpx.MockTransport(receiver))
    client.app.state.http = http  # type: ignore[attr-defined]
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    c = sign_in_client(client, db, owner, org_id)
    bad = c.post(
        "/api/v1/org/outgoing-webhooks", json={"name": "x", "url": "https://hooks.internal/x"}
    )
    assert bad.status_code == 400 and bad.json()["code"] == "unsafe_url"
    assert (
        c.post(
            "/api/v1/org/outgoing-webhooks", json={"name": "x", "url": "http://example.com/hook"}
        ).status_code
        == 400
    )
    r = c.post(
        "/api/v1/org/outgoing-webhooks",
        json={
            "name": "SIEM",
            "url": "https://hooks.example.com/costlens?token=abc",
            "events": ["budget_alert"],
        },
    )
    assert r.status_code == 201, r.text
    hook = r.json()
    secret = hook["secret"]
    assert secret.startswith("whsec_") and hook["url_hint"] == "https://hooks.example.com/…"
    assert c.get("/api/v1/org/outgoing-webhooks").json()[0]["secret"] is None  # shown once

    # A test ping goes out straight away, signed.
    ping = c.post(f"/api/v1/org/outgoing-webhooks/{hook['id']}/test").json()
    assert ping["status"] == "sent" and ping["event"] == "ping"
    req = receiver.requests[0]
    assert req.headers["x-costlens-event"] == "ping"
    assert req.headers["x-costlens-delivery"] == ping["id"]
    now = int(datetime.now(UTC).timestamp())
    assert webhooks_out.verify(secret, req.content, req.headers["x-costlens-signature"], now)

    # Events reach subscribed webhooks only; failures retry with back-off, then stop.
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        notify(s, "policy_violation", [Message(title="not subscribed", text="x")])
        notify(
            s, "budget_alert", [Message(title="Budget 90%", text="key=AKIAABCDEFGHIJKLMNOP spent")]
        )
        pending = s.scalars(
            select(WebhookDelivery).where(WebhookDelivery.status == "pending")
        ).all()
        assert [d.event for d in pending] == ["budget_alert"]
        payload = pending[0].payload
        assert payload["data"]["title"] == "Budget 90%" and "AKIA" not in payload["data"]["text"]
        receiver.answers = [500, 302, 200]
        t = datetime.now(UTC) + timedelta(seconds=1)  # just after the delivery was queued
        assert webhooks_out.deliver_due(s, http, public_resolver, t) == 0  # 500
        d = s.get(WebhookDelivery, pending[0].id)
        assert d is not None and d.attempts == 1 and d.next_attempt_at == t + timedelta(minutes=1)
        assert webhooks_out.deliver_due(s, http, public_resolver, t + timedelta(seconds=30)) == 0
        assert d.attempts == 1  # not due yet
        t += timedelta(minutes=1)
        assert webhooks_out.deliver_due(s, http, public_resolver, t) == 0  # 302 is not followed
        assert d.last_status == 302 and d.next_attempt_at == t + timedelta(minutes=5)
        t += timedelta(minutes=5)
        assert webhooks_out.deliver_due(s, http, public_resolver, t) == 1
        assert d.status == "sent" and d.delivered_at == t
        body = json.loads(receiver.requests[-1].content)
        assert body["event"] == "budget_alert" and body["id"] == str(d.id)

        notify(s, "budget_alert", [Message(title="again", text="y")])
        d2 = s.scalars(select(WebhookDelivery).where(WebhookDelivery.status == "pending")).one()
        receiver.answers = [503] * 10
        t = datetime.now(UTC) + timedelta(seconds=1)  # just after the delivery was queued
        for _ in range(webhooks_out.MAX_ATTEMPTS):
            webhooks_out.deliver_due(s, http, public_resolver, t)
            t += timedelta(hours=7)
        assert d2.status == "failed" and d2.attempts == webhooks_out.MAX_ATTEMPTS
        d2_id = str(d2.id)

    deliveries = c.get(f"/api/v1/org/outgoing-webhooks/{hook['id']}/deliveries").json()
    assert {x["status"] for x in deliveries} == {"sent", "failed"}
    receiver.answers = [200]
    again = c.post(f"/api/v1/org/outgoing-webhooks/deliveries/{d2_id}/retry").json()
    assert again["status"] == "sent" and again["attempts"] == 1
    assert (
        c.post(f"/api/v1/org/outgoing-webhooks/deliveries/{d2_id}/retry").json()["code"]
        == "already_sent"
    )
    rotated = c.post(f"/api/v1/org/outgoing-webhooks/{hook['id']}/rotate-secret").json()
    assert rotated["secret"] != secret
    c.patch(f"/api/v1/org/outgoing-webhooks/{hook['id']}", json={"enabled": False})
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        notify(s, "budget_alert", [Message(title="off", text="z")])
        assert (
            s.scalars(select(WebhookDelivery).where(WebhookDelivery.status == "pending")).all()
            == []
        )
    assert c.delete(f"/api/v1/org/outgoing-webhooks/{hook['id']}").status_code == 204
