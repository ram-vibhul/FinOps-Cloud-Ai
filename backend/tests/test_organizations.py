from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from costlens.accounts.roles import Role
from costlens.db import Database
from tests.helpers import add_to_org, make_org, make_user, sign_in_client


@pytest.fixture
def team(client: TestClient, db: Database):
    org_id, owner = make_org(db, "Acme", "owner@acme.com")
    admin = add_to_org(db, org_id, "admin@acme.com", Role.ADMIN)
    member = add_to_org(db, org_id, "member@acme.com", Role.MEMBER)
    viewer = add_to_org(db, org_id, "viewer@acme.com", Role.VIEWER)
    return {
        "c": client,
        "db": db,
        "org": org_id,
        "owner": owner,
        "admin": admin,
        "member": member,
        "viewer": viewer,
    }


def as_(team, who: str) -> TestClient:
    return sign_in_client(team["c"], team["db"], team[who], team["org"])


def test_create_and_switch(team) -> None:
    c = as_(team, "member")
    r = c.post("/api/v1/organizations", json={"name": "Side Project"})
    assert r.status_code == 201
    me = c.get("/api/v1/auth/me").json()
    assert me["active_organization_id"] == r.json()["id"] and me["role"] == "owner"
    assert (
        c.post("/api/v1/auth/switch", json={"organization_id": str(team["org"])}).status_code == 204
    )
    assert c.get("/api/v1/auth/me").json()["role"] == "member"


def test_cannot_switch_to_foreign_org(team) -> None:
    other, _ = make_org(team["db"], "Other", "x@other.com")
    r = as_(team, "member").post("/api/v1/auth/switch", json={"organization_id": str(other)})
    assert r.status_code == 404


def test_org_settings(team) -> None:
    assert as_(team, "viewer").get("/api/v1/org").json()["name"] == "Acme"
    assert as_(team, "member").patch("/api/v1/org", json={"name": "X"}).status_code == 403
    r = as_(team, "admin").patch(
        "/api/v1/org", json={"name": "Acme Inc", "default_currency": "INR"}
    )
    assert r.json()["name"] == "Acme Inc" and r.json()["default_currency"] == "INR"
    assert (
        as_(team, "admin").patch("/api/v1/org", json={"default_currency": "rupee"}).status_code
        == 422
    )


def test_members_list_and_roles(team) -> None:
    members = as_(team, "viewer").get("/api/v1/org/members").json()
    assert [m["role"] for m in members] == ["admin", "member", "owner", "viewer"]
    c = as_(team, "admin")
    r = c.patch(f"/api/v1/org/members/{team['viewer']}", json={"role": "member"})
    assert r.json()["role"] == "member"
    assert (
        c.patch(f"/api/v1/org/members/{team['owner']}", json={"role": "viewer"}).status_code == 403
    )
    assert (
        as_(team, "member")
        .patch(f"/api/v1/org/members/{team['viewer']}", json={"role": "admin"})
        .status_code
        == 403
    )


def test_remove_and_leave(team) -> None:
    assert as_(team, "admin").delete(f"/api/v1/org/members/{team['viewer']}").status_code == 204
    c = as_(team, "member")
    assert c.delete(f"/api/v1/org/members/{team['member']}").status_code == 204
    me = c.get("/api/v1/auth/me").json()
    assert me["active_organization_id"] is None and me["organizations"] == []
    r = as_(team, "owner").delete(f"/api/v1/org/members/{team['owner']}")
    assert r.status_code == 409 and r.json()["code"] == "last_owner"


def test_transfer_ownership(team) -> None:
    assert (
        as_(team, "admin")
        .post("/api/v1/org/transfer-ownership", json={"user_id": str(team["member"])})
        .status_code
        == 403
    )
    c = as_(team, "owner")
    assert (
        c.post("/api/v1/org/transfer-ownership", json={"user_id": str(team["member"])}).status_code
        == 204
    )
    roles = {m["email"]: m["role"] for m in c.get("/api/v1/org/members").json()}
    assert roles["member@acme.com"] == "owner" and roles["owner@acme.com"] == "admin"
    stranger = make_user(team["db"], "s@x.com")
    c = as_(team, "member")
    assert (
        c.post("/api/v1/org/transfer-ownership", json={"user_id": str(stranger)}).status_code == 404
    )


def test_removed_member_loses_access_immediately(team) -> None:
    c = as_(team, "viewer")
    assert c.get("/api/v1/org").status_code == 200
    token = c.cookies.get("costlens_session")
    as_(team, "owner").delete(f"/api/v1/org/members/{team['viewer']}")
    c.cookies.clear()
    c.cookies.set("costlens_session", token or "")
    assert c.get("/api/v1/org").status_code == 409  # no organisation any more
