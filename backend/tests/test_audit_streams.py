"""Audit-log streaming to a SIEM (11.42): formats, signing, cursor and retries."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ccradar.audit import streams
from ccradar.core import webhooks_out
from ccradar.db import Database
from ccradar.db.base import utcnow
from ccradar.db.tenant import set_tenant
from ccradar.models import AuditEvent, AuditStream
from tests.conftest import public_resolver
from tests.helpers import make_org, sign_in_client


class Receiver:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.answers: list[int] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "login.microsoftonline.com":
            return httpx.Response(200, json={"access_token": "entra-token", "expires_in": 3600})
        return httpx.Response(self.answers.pop(0) if self.answers else 200)


def _events(db: Database, org_id: uuid.UUID, n: int, start: datetime) -> list[uuid.UUID]:
    ids = []
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        for i in range(n):
            e = AuditEvent(
                org_id=org_id,
                occurred_at=start + timedelta(seconds=i),
                actor_type="user",
                actor_label="o@acme.com",
                action="budget.updated",
                target_type="budget",
                target_id=str(i),
                summary=f"Budget {i} changed",
                changes={"amount": [i, i + 1]},
                ip="203.0.113.7",
            )
            s.add(e)
            s.flush()
            ids.append(e.id)
    return ids


def _stream(
    db: Database,
    org_id: uuid.UUID,
    kind: str,
    settings: dict,
    secret: str | None = "tok",  # noqa: S107 - a fake token
) -> uuid.UUID:
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        st = AuditStream(
            org_id=org_id,
            name=kind,
            kind=kind,
            settings=settings,
            secret=secret,
            signing_secret="whsec_test",
            cursor_at=None,
        )
        s.add(st)
        s.flush()
        return st.id


def _run(db: Database, org_id: uuid.UUID, stream_id: uuid.UUID, tr: streams.Transport, now=None):
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        st = s.get(AuditStream, stream_id)
        assert st is not None
        sent = streams.run_stream(s, st, tr, now)
        return sent, st.failures, st.last_error, st.next_attempt_at, st.cursor_id


@pytest.fixture
def receiver() -> Receiver:
    return Receiver()


@pytest.fixture
def transport(receiver: Receiver) -> streams.Transport:
    return streams.Transport(
        http=httpx.Client(transport=httpx.MockTransport(receiver)), resolver=public_resolver
    )


def test_splunk_batches_are_signed_and_the_cursor_moves(
    db: Database, receiver: Receiver, transport: streams.Transport
) -> None:
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    ids = _events(db, org_id, 3, utcnow() - timedelta(hours=1))
    sid = _stream(
        db, org_id, "splunk_hec", {"url": "https://splunk.example.com:8088", "index": "sec"}
    )
    sent, failures, _, _, cursor = _run(db, org_id, sid, transport)
    assert sent >= 3 and failures == 0
    req = receiver.requests[0]
    assert str(req.url) == "https://splunk.example.com:8088/services/collector/event"
    assert req.headers["authorization"] == "Splunk tok"
    now = int(datetime.now(UTC).timestamp())
    assert webhooks_out.verify(
        "whsec_test", req.content, req.headers["x-cloudcostradar-signature"], now
    )
    lines = [json.loads(x) for x in req.content.decode().splitlines()]
    mine = [x for x in lines if x["event"]["action"] == "budget.updated"]
    assert [x["event"]["id"] for x in mine] == [str(i) for i in ids]
    assert mine[0]["index"] == "sec" and mine[0]["sourcetype"] == "cloudcostradar:audit"
    assert mine[0]["event"]["changes"] == {"amount": [0, 1]}
    # Nothing new: nothing sent.
    before = len(receiver.requests)
    assert _run(db, org_id, sid, transport)[0] == 0
    assert len(receiver.requests) == before
    assert cursor is not None


def test_failures_back_off_and_resume_from_the_same_place(
    db: Database, receiver: Receiver, transport: streams.Transport
) -> None:
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    _events(db, org_id, 2, utcnow() - timedelta(hours=1))
    sid = _stream(db, org_id, "webhook", {"url": "https://siem.example.com/in"}, None)
    receiver.answers = [503, 500]
    t0 = utcnow()
    sent, failures, error, next_at, cursor = _run(db, org_id, sid, transport, t0)
    assert (sent, failures, error, cursor) == (0, 1, "answered 503", None)
    assert next_at is not None and next_at - t0 == timedelta(minutes=1)
    with db.session() as s:
        set_tenant(s, bypass=True)
        assert [x.id for x in streams.due(s, t0)] == []  # waiting for its retry
        assert [x.id for x in streams.due(s, t0 + timedelta(minutes=2))] == [sid]
    sent, failures, _, next_at, _ = _run(db, org_id, sid, transport, t0 + timedelta(minutes=2))
    assert failures == 2 and next_at == t0 + timedelta(minutes=7)
    sent, failures, error, next_at, cursor = _run(
        db, org_id, sid, transport, t0 + timedelta(minutes=8)
    )
    assert sent >= 2 and failures == 0 and error is None and next_at is None and cursor
    bodies = [json.loads(r.content) for r in receiver.requests]
    first = bodies[0]["events"]
    for later in bodies[1:]:  # each retry starts from the same place: nothing lost
        assert later["events"][: len(first)] == first
    assert receiver.requests[2].headers["x-cloudcostradar-event"] == "audit.batch"


def test_recent_events_wait_to_settle(db: Database, transport: streams.Transport) -> None:
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    now = utcnow()
    _events(db, org_id, 1, now - timedelta(seconds=30))
    sid = _stream(db, org_id, "webhook", {"url": "https://siem.example.com/in"}, None)
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        st = s.get(AuditStream, sid)
        assert st is not None
        assert not any(e.action == "budget.updated" for e in streams.pending(s, st, now))
        later = streams.pending(s, st, now + timedelta(minutes=3))
        assert any(e.action == "budget.updated" for e in later)


def test_sentinel_gets_a_token_and_posts_to_the_rule(
    db: Database, receiver: Receiver, transport: streams.Transport
) -> None:
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    _events(db, org_id, 1, utcnow() - timedelta(hours=1))
    settings = {
        "tenant_id": str(uuid.uuid4()),
        "client_id": str(uuid.uuid4()),
        "endpoint": "https://acme-x1.centralindia-1.ingest.monitor.azure.com",
        "dcr_id": "dcr-0123456789abcdef",
        "stream": "Custom-CloudCostRadarAudit_CL",
    }
    sid = _stream(db, org_id, "sentinel", settings, "client-secret")
    assert _run(db, org_id, sid, transport)[1] == 0
    token_req, post = receiver.requests[0], receiver.requests[1]
    assert token_req.url.path.endswith("/oauth2/v2.0/token")
    assert b"client_secret=client-secret" in token_req.content
    assert b"monitor.azure.com" in token_req.content
    assert post.url.path == (
        "/dataCollectionRules/dcr-0123456789abcdef/streams/Custom-CloudCostRadarAudit_CL"
    )
    assert post.url.params["api-version"] == "2023-01-01"
    assert post.headers["authorization"] == "Bearer entra-token"
    rows = json.loads(post.content)
    row = next(r for r in rows if r["Action"] == "budget.updated")
    assert row["SourceIp"] == "203.0.113.7" and "TimeGenerated" in row


def test_datadog_uses_the_site_intake(
    db: Database, receiver: Receiver, transport: streams.Transport
) -> None:
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    _events(db, org_id, 1, utcnow() - timedelta(hours=1))
    sid = _stream(db, org_id, "datadog", {"site": "datadoghq.eu", "tags": "env:prod"}, "dd-key")
    assert _run(db, org_id, sid, transport)[1] == 0
    req = receiver.requests[0]
    assert str(req.url) == "https://http-intake.logs.datadoghq.eu/api/v2/logs"
    assert req.headers["dd-api-key"] == "dd-key"
    item = next(i for i in json.loads(req.content) if i["audit"]["action"] == "budget.updated")
    assert item["ddsource"] == "cloudcostradar" and "env:prod" in item["ddtags"]
    assert "action:budget.updated" in item["ddtags"] and item["message"] == "Budget 0 changed"


def test_syslog_frames_are_rfc5424_and_signed(db: Database) -> None:
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    _events(db, org_id, 2, utcnow() - timedelta(hours=1))
    sid = _stream(db, org_id, "syslog", {"host": "logs.example.com", "port": 6514}, None)
    sent: list[tuple] = []
    tr = streams.Transport(
        http=httpx.Client(),
        resolver=public_resolver,
        syslog=lambda host, port, addresses, frames: sent.append((host, port, addresses, frames)),
    )
    assert _run(db, org_id, sid, tr)[1] == 0
    host, port, addresses, frames = sent[0]
    assert (host, port, addresses) == ("logs.example.com", 6514, ["93.184.216.34"])
    frame = next(f for f in frames if b"budget.updated" in f)
    length, rest = frame.split(b" ", 1)
    assert int(length) == len(rest)  # octet counting
    assert rest.startswith(b"<110>1 ")
    header, body = rest.split(b"] \xef\xbb\xbf", 1)
    sig = header.split(b'sig="')[1].rstrip(b'"').decode()
    assert sig == hmac.new(b"whsec_test", body, hashlib.sha256).hexdigest()
    assert json.loads(body)["action"] == "budget.updated"


def test_unsafe_addresses_are_refused(db: Database, transport: streams.Transport) -> None:
    for kind, settings in (
        ("webhook", {"url": "https://siem.internal/in"}),
        ("webhook", {"url": "http://siem.example.com/in"}),
        ("splunk_hec", {"url": "https://splunk.example.com:9999"}),
        ("syslog", {"host": "logs.internal"}),
        (
            "sentinel",
            {
                "tenant_id": "x",
                "client_id": "y",
                "endpoint": "https://a.example.com",
                "dcr_id": "dcr-1",
                "stream": "Custom-x",
            },
        ),
        ("datadog", {"site": "example.com"}),
    ):
        with pytest.raises(streams.StreamError):
            streams.clean_settings(kind, settings, public_resolver)
    # And at send time, if DNS changes to a private address:
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    _events(db, org_id, 1, utcnow() - timedelta(hours=1))
    sid = _stream(db, org_id, "webhook", {"url": "https://siem.internal/in"}, None)
    _, failures, error, _, _ = _run(db, org_id, sid, transport)
    assert failures == 1 and error and "not allowed" in error


def test_api_manages_streams_and_hides_secrets(client: TestClient, db: Database) -> None:
    receiver = Receiver()
    client.app.state.http = httpx.Client(transport=httpx.MockTransport(receiver))  # type: ignore[attr-defined]
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    c = sign_in_client(client, db, owner, org_id)
    assert c.get("/api/v1/org/audit-streams/kinds").json()["kinds"]["sentinel"]
    missing = c.post(
        "/api/v1/org/audit-streams",
        json={"name": "S", "kind": "splunk_hec", "settings": {"url": "https://s.example.com"}},
    )
    assert missing.status_code == 400 and missing.json()["code"] == "secret_required"
    r = c.post(
        "/api/v1/org/audit-streams",
        json={
            "name": "Splunk",
            "kind": "splunk_hec",
            "settings": {"url": "https://s.example.com/"},
            "secret": "hec-token",
        },
    )
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["signing_secret"].startswith("whsec_") and out["has_secret"]
    assert out["settings"] == {"url": "https://s.example.com"}
    listed = c.get("/api/v1/org/audit-streams").json()[0]
    assert listed["signing_secret"] is None and "secret" not in listed
    assert "hec-token" not in json.dumps(listed)

    test = c.post(f"/api/v1/org/audit-streams/{out['id']}/test").json()
    assert test == {"ok": True, "error": None}
    assert json.loads(receiver.requests[-1].content)["event"]["action"] == "audit.test"
    receiver.answers = [403]
    bad = c.post(f"/api/v1/org/audit-streams/{out['id']}/test").json()
    assert bad == {"ok": False, "error": "answered 403"}

    rotated = c.post(f"/api/v1/org/audit-streams/{out['id']}/rotate-signing-secret").json()
    assert rotated["signing_secret"] != out["signing_secret"]
    off = c.patch(f"/api/v1/org/audit-streams/{out['id']}", json={"enabled": False}).json()
    assert off["enabled"] is False
    with db.session() as s:
        set_tenant(s, bypass=True)
        assert s.scalars(select(AuditStream)).one().secret == "hec-token"  # stored encrypted
        assert streams.due(s, utcnow()) == []  # disabled: not sent
    # Creating, changing and deleting streams is itself in the audit log.
    assert c.delete(f"/api/v1/org/audit-streams/{out['id']}").status_code == 204
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        actions = {a for a in s.scalars(select(AuditEvent.action)) if a.startswith("audit_stream")}
    assert {"audit_stream.created", "audit_stream.deleted"} <= actions


def test_viewers_cannot_manage_streams(client: TestClient, db: Database) -> None:
    from ccradar.accounts.roles import Role
    from tests.helpers import add_to_org, make_user

    org_id, _ = make_org(db, "Acme", "o@acme.com")
    viewer = make_user(db, "v@acme.com")
    add_to_org(db, org_id, "v@acme.com", Role.VIEWER)
    c = sign_in_client(client, db, viewer, org_id)
    assert c.get("/api/v1/org/audit-streams").status_code == 403
