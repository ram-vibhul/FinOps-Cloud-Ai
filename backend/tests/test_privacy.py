from __future__ import annotations

import io
import json
import uuid
import zipfile
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from costlens.accounts.roles import Role
from costlens.db import Database
from costlens.db.base import Base, utcnow
from costlens.db.tenant import set_tenant, tenant_tables
from costlens.jobs import load_all_jobs
from costlens.models import AuditEvent, OrgIdentityProvider, User
from costlens.privacy import service
from tests.helpers import add_to_org, make_org, sign_in_client


@pytest.fixture
def team(client: TestClient, db: Database):
    load_all_jobs()
    org_id, owner = make_org(db, "Acme", "owner@acme.com")
    member = add_to_org(db, org_id, "member@acme.com", Role.MEMBER)
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        s.add(
            OrgIdentityProvider(
                kind="oidc",
                display_name="SSO",
                issuer="https://sso.example.com",
                client_id="cid",
                client_secret="never-export-me",
                trust_email=False,
                enabled=True,
            )
        )
    return {"c": client, "db": db, "org": org_id, "owner": owner, "member": member}


def as_(team, who) -> TestClient:
    return sign_in_client(team["c"], team["db"], team[who], team["org"])


# --- personal data ------------------------------------------------------------


def test_personal_export(team) -> None:
    c = as_(team, "member")
    c.patch("/api/v1/org/members/x", json={"role": "viewer"})  # some activity (fails, 404)
    r = c.get("/api/v1/me/export")
    assert r.headers["content-disposition"].startswith("attachment")
    data = r.json()
    assert data["profile"]["email"] == "member@acme.com"
    assert data["organizations"] == [
        {
            "name": "Acme",
            "slug": "acme",
            "role": "member",
            "joined_at": data["organizations"][0]["joined_at"],
        }
    ]
    assert data["sign_in_methods"][0]["provider"] == "test"
    assert len(data["sessions"]) >= 1


def test_delete_account_requires_confirmation_and_pseudonymises(team) -> None:
    c = as_(team, "owner")
    c.patch(f"/api/v1/org/members/{team['member']}", json={"role": "admin"})  # owner acts
    c = as_(team, "member")
    c.patch("/api/v1/org", json={"name": "Acme by member"})  # member (now admin) acts
    assert (
        c.request("DELETE", "/api/v1/me", json={"confirm_email": "wrong@x.com"}).status_code == 400
    )
    r = c.request("DELETE", "/api/v1/me", json={"confirm_email": "Member@acme.com"})
    assert r.status_code == 204
    assert c.get("/api/v1/auth/me").status_code == 401
    with team["db"].session() as s:
        set_tenant(s, bypass=True)
        assert s.get(User, team["member"]) is None
        trail = s.scalars(
            select(AuditEvent).where(AuditEvent.actor_user_id == team["member"])
        ).all()
        assert trail and all(e.actor_label == "deleted user" and e.ip is None for e in trail)
        actions = set(s.scalars(select(AuditEvent.action)))
        assert "member.account_deleted" in actions


def test_sole_owner_cannot_delete_account(team) -> None:
    r = as_(team, "owner").request("DELETE", "/api/v1/me", json={"confirm_email": "owner@acme.com"})
    assert r.status_code == 409 and r.json()["code"] == "sole_owner"
    assert "Acme" in r.json()["detail"]


# --- organisation export ------------------------------------------------------------


def test_org_export_zip(team) -> None:
    c = as_(team, "owner")
    r = c.post("/api/v1/org/exports")
    assert r.status_code == 202
    export_id = r.json()["id"]
    listed = c.get("/api/v1/org/exports").json()
    assert listed[0]["status"] == "ready" and listed[0]["download_url"]
    zipped = c.get(f"/api/v1/org/exports/{export_id}/download")
    assert zipped.headers["content-type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(zipped.content))
    names = set(archive.namelist())
    assert {"manifest.json", "organization.json", "tables/memberships.json"} <= names
    for table in tenant_tables():
        assert f"tables/{table}.json" in names
    everything = b"".join(archive.read(n) for n in names)
    assert b"never-export-me" not in everything  # encrypted secrets excluded
    assert b"token_hash" not in everything
    manifest = json.loads(archive.read("manifest.json"))
    assert manifest["tables"]["memberships"] == 2


def test_only_owners_export(team) -> None:
    assert as_(team, "member").post("/api/v1/org/exports").status_code == 403


def test_expired_export_unavailable(team) -> None:
    c = as_(team, "owner")
    export_id = c.post("/api/v1/org/exports").json()["id"]
    with team["db"].session() as s:
        set_tenant(s, bypass=True)
        table = Base.metadata.tables["data_exports"]
        s.execute(update(table).values(expires_at=utcnow() - timedelta(minutes=1)))
    assert c.get(f"/api/v1/org/exports/{export_id}/download").status_code == 404
    with team["db"].session() as s:
        assert service.cleanup_expired_exports(s, c.app.state.storage) == 1  # type: ignore[attr-defined]


def test_other_org_cannot_download(team) -> None:
    export_id = as_(team, "owner").post("/api/v1/org/exports").json()["id"]
    other, boss = make_org(team["db"], "Other", "boss@other.com")
    c = sign_in_client(team["c"], team["db"], boss, other)
    assert c.get(f"/api/v1/org/exports/{export_id}/download").status_code == 404


# --- organisation deletion ------------------------------------------------------------


def test_delete_restore_and_purge(team) -> None:
    c = as_(team, "owner")
    assert c.request("DELETE", "/api/v1/org", json={"confirm_slug": "nope"}).status_code == 400
    r = c.request("DELETE", "/api/v1/org", json={"confirm_slug": "acme"})
    assert r.status_code == 202 and "purge_after" in r.json()
    # Hidden from everyone immediately.
    assert c.get("/api/v1/auth/me").json()["organizations"] == []
    m = as_(team, "member")
    assert m.get("/api/v1/org").status_code == 409
    # Owner can restore during the grace period; members cannot.
    assert m.post(f"/api/v1/organizations/{team['org']}/restore").status_code == 403
    c = sign_in_client(team["c"], team["db"], team["owner"])
    assert c.post(f"/api/v1/organizations/{team['org']}/restore").status_code == 204
    assert c.get("/api/v1/org").json()["name"] == "Acme"
    # Delete again and purge after the grace period.
    c.post("/api/v1/org/exports")
    c.request("DELETE", "/api/v1/org", json={"confirm_slug": "acme"})
    storage = c.app.state.storage  # type: ignore[attr-defined]
    with team["db"].session() as s:
        assert service.purge_deleted_orgs(s, storage) == []  # still in grace period
    with team["db"].session() as s:
        purged = service.purge_deleted_orgs(s, storage, now=utcnow() + timedelta(days=31))
        assert purged == [team["org"]]
    with team["db"].session() as s:
        set_tenant(s, bypass=True)
        for name in tenant_tables():
            table = Base.metadata.tables[name]
            count = s.scalar(
                select(func.count()).select_from(table).where(table.c.org_id == team["org"])
            )
            assert count == 0, name
        assert s.get(User, team["owner"]) is not None  # people keep their accounts
    assert not any((storage.root / "exports" / str(team["org"])).glob("*.zip"))


def test_demo_cannot_be_deleted(team) -> None:
    from costlens.demo.seed import ensure_demo
    from costlens.models import Membership

    with team["db"].session() as s:
        demo = ensure_demo(s)
        set_tenant(s, org_id=demo.id)
        s.add(Membership(org_id=demo.id, user_id=team["owner"], role=Role.OWNER))
        demo_id = demo.id
    c = sign_in_client(team["c"], team["db"], team["owner"], demo_id)
    r = c.request("DELETE", "/api/v1/org", json={"confirm_slug": "demo"})
    assert r.status_code == 403


def test_storage_rejects_unsafe_keys(tmp_path) -> None:
    from costlens.core.storage import LocalStorage

    store = LocalStorage(tmp_path)
    store.put("exports/a/b.zip", b"x")
    assert store.get("exports/a/b.zip") == b"x"
    for key in ("../escape", "/etc/passwd", "a/../../b", "bad key!"):
        with pytest.raises(ValueError):
            store.put(key, b"x")
    assert uuid  # keep import
