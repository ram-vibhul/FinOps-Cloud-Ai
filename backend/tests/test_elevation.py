from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from costlens.accounts import elevation
from costlens.accounts.roles import Role
from costlens.db import Database
from costlens.db.base import utcnow
from costlens.db.tenant import set_tenant
from costlens.models import AuditEvent, ElevatedAccess
from tests.helpers import add_to_org, make_org, sign_in_client


def test_request_approve_use_and_expire(client: TestClient, db: Database) -> None:
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    admin = add_to_org(db, org_id, "a@acme.com", Role.ADMIN)
    dev = add_to_org(db, org_id, "dev@acme.com", Role.VIEWER)
    d = sign_in_client(client, db, dev, org_id)
    assert d.put("/api/v1/org/alert-settings", json={"min_impact": 5}).status_code == 403
    bad = d.post(
        "/api/v1/org/elevated-access", json={"role": "viewer", "minutes": 60, "reason": "need it"}
    )
    assert bad.json()["code"] == "not_higher"
    r = d.post(
        "/api/v1/org/elevated-access",
        json={"role": "admin", "minutes": 60, "reason": "fix the alert settings"},
    ).json()
    assert r["status"] == "requested"
    assert (
        d.post(f"/api/v1/org/elevated-access/{r['id']}/approve").status_code == 403
    )  # not an admin
    a = sign_in_client(client, db, admin, org_id)
    ok = a.post(f"/api/v1/org/elevated-access/{r['id']}/approve").json()
    assert ok["status"] == "active" and ok["decided_by"] == "a@acme.com" and ok["expires_at"]
    assert (
        a.post(f"/api/v1/org/elevated-access/{r['id']}/approve").json()["code"] == "already_decided"
    )
    d = sign_in_client(client, db, dev, org_id)
    assert (
        d.put("/api/v1/org/alert-settings", json={"min_impact": 5}).status_code == 200
    )  # elevated now
    assert [x["id"] for x in d.get("/api/v1/org/elevated-access").json()] == [
        r["id"]
    ]  # own requests only
    # Owner access needs an owner; an admin cannot grant it.
    a = sign_in_client(client, db, admin, org_id)
    no = a.post(
        "/api/v1/org/elevated-access/grant",
        json={"user_id": str(dev), "role": "owner", "minutes": 30, "reason": "billing change"},
    )
    assert no.status_code == 403
    # Time passes: the grant stops working on its own and is marked expired.
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        g = s.scalars(select(ElevatedAccess)).one()
        g.expires_at = utcnow() - timedelta(minutes=1)
    d = sign_in_client(client, db, dev, org_id)
    assert d.put("/api/v1/org/alert-settings", json={"min_impact": 6}).status_code == 403
    with db.session() as s:
        set_tenant(s, bypass=True)
        assert elevation.expire(s) == 1
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        assert s.scalars(select(ElevatedAccess)).one().status == "expired"
        actions = [
            e.action
            for e in s.scalars(
                select(AuditEvent).where(AuditEvent.target_type == "elevated_access")
            )
        ]
    assert len(actions) >= 3  # requested, approved, expired
    o = sign_in_client(client, db, owner, org_id)
    granted = o.post(
        "/api/v1/org/elevated-access/grant",
        json={"user_id": str(dev), "role": "member", "minutes": 15, "reason": "one quick change"},
    ).json()
    assert granted["status"] == "active"
    d = sign_in_client(client, db, dev, org_id)
    assert (
        d.post(f"/api/v1/org/elevated-access/{granted['id']}/revoke").json()["status"] == "revoked"
    )  # can end own access


def test_cannot_approve_own_or_grant_self(db: Database) -> None:
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        import pytest

        from costlens.core.errors import PermissionDeniedError

        with pytest.raises(PermissionDeniedError):
            elevation.grant(s, org_id, owner, Role.OWNER, owner, Role.OWNER, 30, "self")
