from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from costlens.accounts.roles import Role
from costlens.changes import cloudtrail, github
from costlens.changes.service import ChangeIn, cost_effect, record, related_events
from costlens.costs.anomalies import detect
from costlens.costs.ingest import CostLine, replace_billing_period
from costlens.db import Database
from costlens.db.tenant import set_tenant
from costlens.models import ChangeEvent, CloudConnection, CostAnomaly
from tests.fakes.aws import FakeAws, FakeCloudTrail
from tests.helpers import add_to_org, make_org, sign_in_client

END = date(2026, 11, 14)
STEP = date(2026, 11, 4)


def load(db: Database, org_id) -> None:
    lines = []
    for i in range(60):
        day = END - timedelta(days=59 - i)
        ec2 = 100.0 if day < STEP else 140.0  # step change after a deployment
        red = 200.0 if day < END else 1200.0
        lines.append(
            CostLine(
                charge_date=day,
                provider="aws",
                service_name="EC2",
                sub_account_id="1",
                billed_cost=Decimal(str(ec2)),
                effective_cost=Decimal(str(ec2)),
                billing_currency="USD",
            )
        )
        lines.append(
            CostLine(
                charge_date=day,
                provider="aws",
                service_name="Redshift",
                sub_account_id="2",
                billed_cost=Decimal(str(red)),
                effective_cost=Decimal(str(red)),
                billing_currency="USD",
            )
        )
    months: dict[date, list] = {}
    for line in lines:
        months.setdefault(line.charge_date.replace(day=1), []).append(line)
    with db.session() as s:
        for month, items in months.items():
            replace_billing_period(
                s, org_id=org_id, source_kind="t", source_key="t", billing_period=month, lines=items
            )


@pytest.fixture
def env(client: TestClient, db: Database):
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    viewer = add_to_org(db, org_id, "v@acme.com", Role.VIEWER)
    load(db, org_id)
    return {"c": client, "db": db, "org": org_id, "owner": owner, "viewer": viewer}


def test_record_is_idempotent_by_external_id(env) -> None:
    with env["db"].session() as s:
        set_tenant(s, org_id=env["org"])
        change = ChangeIn(
            title="deploy v1", external_id="x1", occurred_at=datetime(2026, 11, 4, 9, tzinfo=UTC)
        )
        first, created = record(s, env["org"], change, source="api")
        again, created_again = record(
            s, env["org"], change.model_copy(update={"title": "deploy v1 (fixed)"}), source="api"
        )
    assert (
        created
        and not created_again
        and first.id == again.id
        and again.title == "deploy v1 (fixed)"
    )


def test_cost_effect(env) -> None:
    with env["db"].session() as s:
        set_tenant(s, org_id=env["org"])
        event, _ = record(
            s,
            env["org"],
            ChangeIn(
                title="checkout v2",
                service="EC2",
                occurred_at=datetime(2026, 11, 3, 12, tzinfo=UTC),
            ),
            source="api",
        )
        effect = cost_effect(s, env["org"], event)
    assert effect == {
        "scope": "EC2",
        "before_per_day": 100.0,
        "after_per_day": 140.0,
        "change_per_day": 40.0,
        "change_pct": 40.0,
        "days_after": 7,
    }


def test_related_events_for_anomaly(env) -> None:
    with env["db"].session() as s:
        set_tenant(s, org_id=env["org"])
        record(
            s,
            env["org"],
            ChangeIn(
                title="ResizeCluster",
                service="Redshift",
                occurred_at=datetime(2026, 11, 13, 15, tzinfo=UTC),
            ),
            source="cloudtrail",
        )
        record(
            s,
            env["org"],
            ChangeIn(
                title="Unrelated EC2 change",
                service="EC2",
                occurred_at=datetime(2026, 11, 13, 15, tzinfo=UTC),
            ),
            source="api",
        )
        record(
            s,
            env["org"],
            ChangeIn(
                title="Old change",
                service="Redshift",
                occurred_at=datetime(2026, 10, 1, tzinfo=UTC),
            ),
            source="api",
        )
    with env["db"].session() as s:
        detect(s, env["org"])
    with env["db"].session() as s:
        set_tenant(s, org_id=env["org"])
        anomaly = s.scalar(select(CostAnomaly).where(CostAnomaly.key == "Redshift"))
        assert anomaly is not None
        titles = [e.title for e in related_events(s, anomaly)]
    assert titles == ["ResizeCluster"]
    c = sign_in_client(env["c"], env["db"], env["viewer"], env["org"])
    api_titles = [e["title"] for e in c.get(f"/api/v1/costs/anomalies/{anomaly.id}/changes").json()]
    assert api_titles == ["ResizeCluster"]


def test_api_create_list_timeline(env) -> None:
    c = sign_in_client(env["c"], env["db"], env["owner"], env["org"])
    r = c.post(
        "/api/v1/changes",
        json={
            "title": "checkout v2",
            "service": "EC2",
            "kind": "deployment",
            "occurred_at": "2026-11-03T12:00:00Z",
            "url": "https://ci.example/1",
        },
    )
    assert r.status_code == 201
    assert (
        c.post("/api/v1/changes", json={"title": "x", "url": "javascript:alert(1)"}).status_code
        == 422
    )
    v = sign_in_client(env["c"], env["db"], env["viewer"], env["org"])
    assert v.post("/api/v1/changes", json={"title": "x"}).status_code == 403
    listed = v.get("/api/v1/changes", params={"days": 400}).json()
    assert listed[0]["effect"]["change_pct"] == 40.0
    tl = v.get("/api/v1/costs/timeline", params={"days": 30}).json()
    assert len(tl["periods"]) == 30 and tl["periods"][-1] == END.isoformat()
    assert tl["values"][-1] == 1340.0 and tl["changes"][0]["title"] == "checkout v2"


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


DEPLOY = {
    "deployment_status": {
        "state": "success",
        "created_at": "2026-11-03T12:00:00Z",
        "target_url": "https://github.com/acme/checkout/actions/runs/1",
    },
    "deployment": {"id": 42, "environment": "production", "ref": "v2.3.0", "sha": "abc1234def"},
    "repository": {"full_name": "acme/checkout"},
    "sender": {"login": "ravi"},
}


def test_github_webhook_flow(env) -> None:
    c = sign_in_client(env["c"], env["db"], env["owner"], env["org"])
    hook = c.post("/api/v1/org/webhooks/github", json={"name": "checkout repo"}).json()
    assert hook["secret"] and hook["url"].endswith(f"/api/v1/hooks/github/{hook['id']}")
    assert (
        "secret" not in c.get("/api/v1/org/webhooks").json()[0]
        or c.get("/api/v1/org/webhooks").json()[0]["secret"] is None
    )
    anon = env["c"]
    anon.cookies.clear()
    body = json.dumps(DEPLOY).encode()
    path = f"/api/v1/hooks/github/{hook['id']}"
    headers = {"X-GitHub-Event": "deployment_status", "Content-Type": "application/json"}
    bad = anon.post(path, content=body, headers={**headers, "X-Hub-Signature-256": "sha256=00"})
    assert bad.status_code == 401
    ok = anon.post(
        path, content=body, headers={**headers, "X-Hub-Signature-256": _sign(hook["secret"], body)}
    )
    assert ok.json() == {"status": "recorded"}
    dup = anon.post(
        path, content=body, headers={**headers, "X-Hub-Signature-256": _sign(hook["secret"], body)}
    )
    assert dup.json() == {"status": "duplicate"}
    ping = json.dumps({"zen": "hi"}).encode()
    ignored = anon.post(
        path,
        content=ping,
        headers={"X-GitHub-Event": "ping", "X-Hub-Signature-256": _sign(hook["secret"], ping)},
    )
    assert ignored.json() == {"status": "ignored"}
    with env["db"].session() as s:
        set_tenant(s, org_id=env["org"])
        event = s.scalar(select(ChangeEvent).where(ChangeEvent.source == "github"))
        assert event is not None and event.title == "acme/checkout deployed v2.3.0 to production"
        assert event.actor == "ravi" and event.environment == "production"
    other, _boss = make_org(env["db"], "Other", "b@other.com")
    with env["db"].session() as s:
        set_tenant(s, org_id=other)
        assert s.scalars(select(ChangeEvent)).all() == []


def test_github_mapping() -> None:
    failed = {**DEPLOY, "deployment_status": {**DEPLOY["deployment_status"], "state": "failure"}}
    assert github.to_change("deployment_status", failed) is None
    release = github.to_change(
        "release",
        {
            "action": "published",
            "repository": {"full_name": "a/b"},
            "release": {
                "id": 7,
                "tag_name": "v1",
                "published_at": "2026-11-01T00:00:00Z",
                "html_url": "https://github.com/a/b/releases/v1",
            },
        },
    )
    assert (
        release is not None
        and release.title == "a/b released v1"
        and release.external_id == "release:7"
    )
    assert github.valid_signature("s", b"x", None) is False


def test_cloudtrail_import(env) -> None:
    trail = FakeCloudTrail(
        events=[
            {
                "EventId": "e1",
                "EventName": "ResizeCluster",
                "EventSource": "redshift.amazonaws.com",
                "EventTime": datetime(2026, 11, 13, 15, tzinfo=UTC),
                "Username": "data-eng",
                "Resources": [{"ResourceName": "warehouse"}],
                "CloudTrailEvent": json.dumps({"recipientAccountId": "2"}),
            },
            {
                "EventId": "e2",
                "EventName": "DescribeInstances",
                "EventSource": "ec2.amazonaws.com",
                "EventTime": datetime(2026, 11, 13, tzinfo=UTC),
            },
        ]
    )
    aws = FakeAws(clients={"cloudtrail": trail})
    role = "arn:aws:iam::222222222222:role/CostLensReadOnly"
    aws.allow(role, "ext")
    with env["db"].session() as s:
        set_tenant(s, org_id=env["org"])
        conn = CloudConnection(
            org_id=env["org"],
            provider="aws",
            name="Data",
            status="active",
            external_id="ext",
            role_arn=role,
            account_id="2",
        )
        s.add(conn)
        s.flush()
        conn_id = conn.id
    with env["db"].session() as s:
        assert cloudtrail.import_connection(s, aws, conn_id) == 1
    with env["db"].session() as s:
        assert cloudtrail.import_connection(s, aws, conn_id) == 0  # de-duplicated
    with env["db"].session() as s:
        set_tenant(s, org_id=env["org"])
        event = s.scalar(select(ChangeEvent))
        assert event is not None
        assert (event.title, event.service, event.account, event.kind) == (
            "ResizeCluster warehouse",
            "Amazon Redshift",
            "2",
            "infrastructure",
        )
    assert trail.calls[0]["LookupAttributes"] == [
        {"AttributeKey": "ReadOnly", "AttributeValue": "false"}
    ]


def test_demo_changes(db: Database) -> None:
    from costlens.demo.seed import ensure_demo, load_all_seeders, seeders

    load_all_seeders()
    if "changes.events" not in seeders():
        pytest.fail("demo seeders not registered")
    with db.session() as s:
        org_id = ensure_demo(s).id
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        titles = [e.title for e in s.scalars(select(ChangeEvent))]
    assert any("ResizeCluster" in t for t in titles)
