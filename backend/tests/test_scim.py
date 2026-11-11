from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import select

from ccradar.accounts.roles import Role
from ccradar.db import Database
from ccradar.db.tenant import set_tenant
from ccradar.models import AuditEvent, Membership, OrgDomain
from tests.helpers import make_org, sign_in_client


def setup(client: TestClient, db: Database, role: str = "member"):
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    c = sign_in_client(client, db, owner, org_id)
    assert c.post("/api/v1/org/scim", json={"default_role": "owner"}).json()["code"] == "bad_role"
    out = c.post("/api/v1/org/scim", json={"default_role": role}).json()
    assert out["token"].startswith("scim_") and out["enabled"] is True
    assert out["base_url"].endswith("/scim/v2") and "token" not in c.get("/api/v1/org/scim").json()
    client.cookies.clear()
    return org_id, owner, {"Authorization": f"Bearer {out['token']}"}


def test_user_lifecycle(client: TestClient, db: Database) -> None:
    org_id, _, h = setup(client, db)
    assert (
        client.get("/scim/v2/Users", headers={"Authorization": "Bearer scim_nope"}).status_code
        == 401
    )
    r = client.post(
        "/scim/v2/Users",
        headers=h,
        json={"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"], "userName": "Dev@Acme.com",
              "name": {"givenName": "Dev", "familyName": "Rao"}, "active": True},
    )  # fmt: skip
    assert r.status_code == 201 and r.headers["content-type"].startswith("application/scim+json")
    user = r.json()
    assert (
        user["userName"] == "dev@acme.com"
        and user["active"] is True
        and user["roles"] == [{"value": "member"}]
    )
    dup = client.post("/scim/v2/Users", headers=h, json={"userName": "dev@acme.com"})
    assert dup.status_code == 409 and dup.json()["scimType"] == "uniqueness"
    found = client.get(
        "/scim/v2/Users", headers=h, params={"filter": 'userName eq "dev@acme.com"'}
    ).json()
    assert found["totalResults"] == 1 and found["Resources"][0]["id"] == user["id"]
    assert (
        client.get("/scim/v2/Users", headers=h, params={"filter": "userName co x"}).status_code
        == 400
    )
    assert client.get("/scim/v2/Users", headers=h).json()["totalResults"] == 2  # owner + dev
    p = client.patch(
        f"/scim/v2/Users/{user['id']}",
        headers=h,
        json={"schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
              "Operations": [{"op": "replace", "value": {"displayName": "Dev R", "active": False}}]},
    )  # fmt: skip
    assert (
        p.status_code == 200 and p.json()["active"] is False and p.json()["displayName"] == "Dev R"
    )
    assert (
        client.get(f"/scim/v2/Users/{user['id']}", headers=h).status_code == 404
    )  # no longer a member
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        assert (
            s.scalar(select(Membership).where(Membership.user_id == uuid.UUID(user["id"]))) is None
        )
        assert any(e.actor_type == "scim" for e in s.scalars(select(AuditEvent)))


def test_owners_are_untouchable_and_domains_enforced(client: TestClient, db: Database) -> None:
    org_id, owner, h = setup(client, db, "admin")
    assert client.delete(f"/scim/v2/Users/{owner}", headers=h).status_code == 403
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        s.add(OrgDomain(org_id=org_id, domain="acme.com", token="t", verified_at=datetime.now(UTC)))
    assert (
        client.post("/scim/v2/Users", headers=h, json={"userName": "x@gmail.com"}).status_code
        == 403
    )
    ok = client.post("/scim/v2/Users", headers=h, json={"userName": "ops@acme.com"}).json()
    assert ok["roles"] == [{"value": "admin"}]
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        m = s.scalars(select(Membership).where(Membership.user_id == owner)).one()
        assert m.role is Role.OWNER


def test_groups_map_to_teams(client: TestClient, db: Database) -> None:
    org_id, owner, h = setup(client, db)
    dev = client.post("/scim/v2/Users", headers=h, json={"userName": "dev@acme.com"}).json()
    g = client.post(
        "/scim/v2/Groups",
        headers=h,
        json={"displayName": "Data", "members": [{"value": dev["id"]}]},
    )
    assert g.status_code == 201 and [m["display"] for m in g.json()["members"]] == ["dev@acme.com"]
    gid = g.json()["id"]
    client.patch(
        f"/scim/v2/Groups/{gid}",
        headers=h,
        json={"Operations": [{"op": "add", "path": "members", "value": [{"value": str(owner)}]}]},
    )
    assert len(client.get(f"/scim/v2/Groups/{gid}", headers=h).json()["members"]) == 2
    client.patch(
        f"/scim/v2/Groups/{gid}",
        headers=h,
        json={"Operations": [{"op": "remove", "path": f'members[value eq "{dev["id"]}"]'}]},
    )
    assert [
        m["value"] for m in client.get(f"/scim/v2/Groups/{gid}", headers=h).json()["members"]
    ] == [str(owner)]
    c = sign_in_client(client, db, owner, org_id)
    assert [t["name"] for t in c.get("/api/v1/org/teams").json()] == ["Data"]
    client.cookies.clear()
    assert client.delete(f"/scim/v2/Groups/{gid}", headers=h).status_code == 204
    assert client.get("/scim/v2/ServiceProviderConfig", headers=h).json()["patch"] == {
        "supported": True
    }
    c = sign_in_client(client, db, owner, org_id)
    assert c.delete("/api/v1/org/scim").status_code == 204
    client.cookies.clear()
    assert client.get("/scim/v2/Users", headers=h).status_code == 401
