from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from ccradar.accounts.roles import Role
from ccradar.costs.ingest import CostLine, replace_billing_period
from ccradar.db import Database
from ccradar.db.tenant import set_tenant
from ccradar.models import Finding
from tests.helpers import add_to_org, make_org, sign_in_client

NOW = datetime(2026, 10, 9, tzinfo=UTC)


def seed(db: Database, org_id) -> None:
    lines = [
        CostLine(
            charge_date=date(2026, 10, 5),
            provider="aws",
            service_name="Amazon EC2",
            sub_account_id=acct,
            resource_id=f"i-{acct}",
            billed_cost=Decimal(v),
            effective_cost=Decimal(v),
            billing_currency="USD",
            tags={"team": team},
        )
        for acct, team, v in (("111", "web", "100"), ("222", "data", "40"))
    ]
    with db.session() as s:
        replace_billing_period(
            s,
            org_id=org_id,
            source_kind="t",
            source_key="t",
            billing_period=date(2026, 10, 1),
            lines=lines,
        )
        set_tenant(s, org_id=org_id)
        for acct in ("111", "222"):
            s.add(
                Finding(
                    org_id=org_id,
                    detector="t",
                    category="waste",
                    dedup_key=f"t:{acct}",
                    overlap_key=acct,
                    title=f"Fix {acct}",
                    savings_monthly=10,
                    currency="USD",
                    risk="low",
                    effort="low",
                    first_seen_at=NOW,
                    last_seen_at=NOW,
                    provider="aws",
                    account_id=acct,
                    resource_id=f"vol-{acct}",
                )
            )


def test_permission_sets_and_account_scope(client: TestClient, db: Database) -> None:
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    dev = add_to_org(db, org_id, "dev@acme.com", Role.ADMIN)
    seed(db, org_id)
    o = sign_in_client(client, db, owner, org_id)
    bad = o.post("/api/v1/org/custom-roles", json={"name": "x", "permissions": ["manage_billing"]})
    assert bad.status_code == 422
    role = o.post(
        "/api/v1/org/custom-roles",
        json={"name": "Web team analyst", "permissions": ["write"], "accounts": ["111"]},
    ).json()
    assert role["permissions"] == ["read", "write"]
    assert (
        o.put(
            f"/api/v1/org/members/{owner}/custom-role", json={"custom_role_id": role["id"]}
        ).json()["code"]
        == "owner"
    )
    assert (
        o.put(
            f"/api/v1/org/members/{dev}/custom-role", json={"custom_role_id": role["id"]}
        ).status_code
        == 200
    )

    d = sign_in_client(client, db, dev, org_id)
    me = d.get("/api/v1/auth/me").json()
    assert me["permissions"] == ["read", "write"] and me["scope"] == {"accounts": ["111"]}
    # Admin built-in role no longer applies: settings are refused.
    assert d.put("/api/v1/org/alert-settings", json={"min_impact": 5}).status_code == 403
    # Explorer: only account 111, even when asking for 222.
    body = {
        "start": "2026-10-01",
        "end": "2026-10-31",
        "group_by": ["account"],
        "granularity": "total",
    }
    out = d.post("/api/v1/costs/explore", json=body).json()
    assert [g["label"] for g in out["groups"]] == ["111"] and out["total"] == 100
    sneaky = d.post("/api/v1/costs/explore", json={**body, "filters": {"account": ["222"]}}).json()
    assert sneaky["total"] == 0
    assert [v["value"] for v in d.get("/api/v1/costs/dimensions/account/values").json()] == ["111"]
    assert d.get("/api/v1/costs/dimensions/resource/values").status_code == 403
    # Savings: only findings in account 111; others are "not found".
    titles = [f["title"] for f in d.get("/api/v1/findings").json()["items"]]
    assert titles == ["Fix 111"]
    o = sign_in_client(client, db, owner, org_id)
    other = next(f for f in o.get("/api/v1/findings").json()["items"] if f["title"] == "Fix 222")
    d = sign_in_client(client, db, dev, org_id)
    assert d.get(f"/api/v1/findings/{other['id']}/history").status_code == 404
    # Views that do not apply the scope refuse a scoped role.
    for path in ("/api/v1/costs/summary", "/api/v1/budgets", "/api/v1/costs/anomalies"):
        assert d.get(path).status_code == 403, path
    # Removing the custom role restores the built-in role.
    o = sign_in_client(client, db, owner, org_id)
    o.put(f"/api/v1/org/members/{dev}/custom-role", json={"custom_role_id": None})
    d = sign_in_client(client, db, dev, org_id)
    assert d.get("/api/v1/costs/summary").status_code == 200
    assert d.put("/api/v1/org/alert-settings", json={"min_impact": 5}).status_code == 200


def test_unscoped_custom_role_and_api_keys(client: TestClient, db: Database) -> None:
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    ops = add_to_org(db, org_id, "ops@acme.com", Role.MEMBER)
    o = sign_in_client(client, db, owner, org_id)
    role = o.post(
        "/api/v1/org/custom-roles",
        json={"name": "Settings keeper", "permissions": ["manage_settings"]},
    ).json()
    o.put(f"/api/v1/org/members/{ops}/custom-role", json={"custom_role_id": role["id"]})
    p = sign_in_client(client, db, ops, org_id)
    assert p.get("/api/v1/costs/summary").status_code == 200  # not scoped: all views
    assert p.put("/api/v1/org/alert-settings", json={"min_impact": 5}).status_code == 200  # granted
    assert (
        p.post("/api/v1/budgets", json={"name": "B", "amount": 1}).status_code == 403
    )  # write not granted
    o = sign_in_client(client, db, owner, org_id)
    assert o.delete(f"/api/v1/org/custom-roles/{role['id']}").status_code == 204
    p = sign_in_client(client, db, ops, org_id)
    assert p.get("/api/v1/auth/me").json()["permissions"] is None
