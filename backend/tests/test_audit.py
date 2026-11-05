from __future__ import annotations

import csv
import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from costlens.accounts.roles import Role
from costlens.db import Database
from costlens.db.tenant import set_tenant
from tests.conftest import POSTGRES_URL
from tests.helpers import add_to_org, make_org, sign_in_client

SPEC = {
    "kind": "oidc",
    "display_name": "Corp SSO",
    "issuer": "https://sso.example.com",
    "client_id": "c",
    "client_secret": "super-secret-value",
}


@pytest.fixture
def team(client: TestClient, db: Database):
    org_id, owner = make_org(db, "Acme", "owner@acme.com")
    member = add_to_org(db, org_id, "member@acme.com", Role.MEMBER)
    return {"c": client, "db": db, "org": org_id, "owner": owner, "member": member}


def as_owner(team) -> TestClient:
    return sign_in_client(team["c"], team["db"], team["owner"], team["org"])


def events(c: TestClient, **params) -> list[dict]:
    r = c.get("/api/v1/org/audit-events", params=params)
    assert r.status_code == 200, r.text
    return r.json()["items"]


def test_org_creation_and_membership_recorded(team) -> None:
    actions = [e["action"] for e in events(as_owner(team))]
    assert "organization.created" in actions
    assert actions.count("membership.created") == 2  # owner + member


def test_role_change_has_before_after_actor_ip_request(team) -> None:
    c = as_owner(team)
    c.patch(
        f"/api/v1/org/members/{team['member']}",
        json={"role": "admin"},
        headers={"X-Request-ID": "req-audit-1"},
    )
    [e] = events(c, action="membership.updated")
    assert e["changes"] == {"role": ["member", "admin"]}
    assert e["actor_label"] == "owner@acme.com" and e["actor_type"] == "user"
    assert e["ip"] == "testclient" and e["request_id"] == "req-audit-1"


def test_secrets_are_redacted(team) -> None:
    c = as_owner(team)
    pid = c.post("/api/v1/org/sso", json=SPEC).json()["id"]
    c.patch(f"/api/v1/org/sso/{pid}", json={"client_secret": "rotated-secret"})
    c.post(f"/api/v1/org/sso/{pid}/test")
    c.delete(f"/api/v1/org/sso/{pid}")
    trail = events(c, action="org_identity_provider")
    assert [e["action"] for e in trail] == [
        "org_identity_provider.deleted",
        "org_identity_provider.tested",
        "org_identity_provider.updated",
        "org_identity_provider.created",
    ]
    text_dump = str(trail)
    assert "super-secret-value" not in text_dump and "rotated-secret" not in text_dump
    assert trail[2]["changes"] == {"client_secret": ["[redacted]", "[redacted]"]}


def test_no_event_for_no_op_update(team) -> None:
    c = as_owner(team)
    c.patch("/api/v1/org", json={"name": "Acme"})
    assert events(c, action="organization.updated") == []
    c.patch("/api/v1/org", json={"name": "Acme Inc"})
    [e] = events(c, action="organization.updated")
    assert e["changes"] == {"name": ["Acme", "Acme Inc"]}


def test_filters_and_pagination(team) -> None:
    c = as_owner(team)
    for i in range(5):
        c.patch("/api/v1/org", json={"name": f"Acme {i}"})
    page1 = c.get("/api/v1/org/audit-events", params={"limit": 2}).json()
    page2 = c.get(
        "/api/v1/org/audit-events", params={"limit": 2, "cursor": page1["next_cursor"]}
    ).json()
    ids1, ids2 = {e["id"] for e in page1["items"]}, {e["id"] for e in page2["items"]}
    assert len(ids1) == 2 and len(ids2) == 2 and not ids1 & ids2
    assert len(events(c, actor="owner@")) >= 5
    assert events(c, actor="nobody") == []
    assert c.get("/api/v1/org/audit-events", params={"cursor": "junk"}).status_code == 400


def test_only_admins_can_read(team) -> None:
    c = sign_in_client(team["c"], team["db"], team["member"], team["org"])
    assert c.get("/api/v1/org/audit-events").status_code == 403


def test_other_org_isolated(team) -> None:
    other, boss = make_org(team["db"], "Other", "boss@other.com")
    c = sign_in_client(team["c"], team["db"], boss, other)
    labels = {e["summary"] for e in events(c)}
    assert all("Acme" not in s for s in labels)


def test_csv_export_neutralises_formulas(team) -> None:
    c = as_owner(team)
    c.patch("/api/v1/org", json={"name": '=HYPERLINK("http://evil")'})
    r = c.get("/api/v1/org/audit-events/export")
    assert r.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0][0] == "occurred_at"
    summaries = [row[6] for row in rows[1:]]
    assert not any(s.startswith("=") for s in summaries)
    assert any("HYPERLINK" in s for s in summaries)


@pytest.mark.skipif(not POSTGRES_URL, reason="needs PostgreSQL")
def test_app_role_cannot_rewrite_audit_history(team) -> None:
    with team["db"].session() as s:
        set_tenant(s, org_id=team["org"])
        with pytest.raises((ProgrammingError, DBAPIError), match="permission denied"):
            s.execute(text("UPDATE audit_events SET summary = 'tampered'"))
        s.rollback()


def test_singular_action_nouns() -> None:
    from costlens.audit.recorder import singular

    assert singular("cost_anomalies") == "cost_anomaly"
    assert singular("memberships") == "membership"
    assert singular("data_exports") == "data_export"
