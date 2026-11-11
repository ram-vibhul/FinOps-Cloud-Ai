from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from ccradar.accounts.roles import Role
from ccradar.db import Database
from ccradar.db.base import utcnow
from ccradar.db.tenant import set_tenant
from ccradar.models import AuditEvent, Membership
from tests.helpers import add_to_org, make_org, sign_in_client


def bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def test_scopes_roles_blocked_paths_and_revocation(client: TestClient, db: Database) -> None:
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    c = sign_in_client(client, db, owner, org_id)
    read = c.post("/api/v1/org/api-keys", json={"name": "dashboards", "scope": "read"}).json()
    write = c.post("/api/v1/org/api-keys", json={"name": "ci", "scope": "write"}).json()
    assert read["key"].startswith("clk_") and read["key_hint"] == read["key"][-6:]
    assert c.get("/api/v1/org/api-keys").json()[0]["key"] is None  # shown once
    assert (
        c.post(
            "/api/v1/org/api-keys",
            json={"name": "x", "expires_at": (utcnow() - timedelta(days=1)).isoformat()},
        ).json()["code"]
        == "bad_expiry"
    )
    client.cookies.clear()  # from here on, no browser session

    # Read key: can read, cannot write.
    assert client.get("/api/v1/budgets", headers=bearer(read["key"])).status_code == 200
    r = client.post(
        "/api/v1/budgets", json={"name": "B", "amount": 10}, headers=bearer(read["key"])
    )
    assert r.status_code == 403
    # Write key can.
    ok = client.post(
        "/api/v1/budgets", json={"name": "B", "amount": 10}, headers=bearer(write["key"])
    )
    assert ok.status_code == 201, ok.text
    # Never: sessions, members, keys themselves.
    for path in ("/api/v1/org/members", "/api/v1/org/api-keys", "/api/v1/auth/me"):
        assert client.get(path, headers=bearer(write["key"])).status_code in (401, 403), path
    assert client.get("/api/v1/budgets", headers=bearer("clk_wrong")).status_code == 401

    # Actions are audited as the key.
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        labels = [e.actor_label for e in s.scalars(select(AuditEvent))]
    assert any(label.startswith("API key ci (") and "o@acme.com" in label for label in labels)

    # Revoked keys stop working.
    c = sign_in_client(client, db, owner, org_id)
    assert c.delete(f"/api/v1/org/api-keys/{read['id']}").status_code == 204
    client.cookies.clear()
    assert client.get("/api/v1/budgets", headers=bearer(read["key"])).status_code == 401


def test_key_follows_its_creator(client: TestClient, db: Database) -> None:
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    admin = add_to_org(db, org_id, "a@acme.com", Role.ADMIN)
    c = sign_in_client(client, db, admin, org_id)
    key = c.post("/api/v1/org/api-keys", json={"name": "ops", "scope": "admin"}).json()["key"]
    client.cookies.clear()
    assert client.get("/api/v1/org/alert-settings", headers=bearer(key)).status_code == 200
    # Demoted to viewer: the admin key now only reads.
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        m = s.scalars(select(Membership).where(Membership.user_id == admin)).one()
        m.role = Role.VIEWER
    assert (
        client.put(
            "/api/v1/org/alert-settings", json={"min_impact": 5}, headers=bearer(key)
        ).status_code
        == 403
    )
    assert client.get("/api/v1/org/alert-settings", headers=bearer(key)).status_code == 200
    # Removed from the organisation: the key stops working.
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        s.delete(s.scalars(select(Membership).where(Membership.user_id == admin)).one())
    assert client.get("/api/v1/org/alert-settings", headers=bearer(key)).status_code == 401


def test_openapi_documents_api_keys(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    assert spec["components"]["securitySchemes"]["apiKey"]["scheme"] == "bearer"
    assert "Settings → API keys" in spec["info"]["description"]
    assert "/api/v1/org/api-keys" in spec["paths"]
