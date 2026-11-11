"""Consolidated view of every cloud and SaaS source in one currency (2.16)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from ccradar.accounts.roles import Role
from ccradar.costs.ingest import CostLine, replace_billing_period
from ccradar.db import Database
from ccradar.db.base import utcnow
from ccradar.db.tenant import set_tenant
from ccradar.models import CloudConnection, ExchangeRate
from tests.helpers import add_to_org, make_org, sign_in_client


def seed(db: Database, org_id) -> date:
    today = utcnow().date()
    first = today.replace(day=1)
    days = [first + timedelta(days=i) for i in range(min(3, today.day))]
    sources = [("aws", "Amazon EC2", "USD", 100), ("azure", "Virtual Machines", "USD", 50)]
    sources.append(("snowflake", "Warehouse", "EUR", 10))
    with db.session() as s:
        set_tenant(s, bypass=True)
        for d in days:  # 1 EUR = 1.0 EUR, 1.25 USD per EUR -> 10 EUR = 12.50 USD
            for cur, rate in (("EUR", 1), ("USD", Decimal("1.25"))):
                if s.get(ExchangeRate, (d, cur)) is None:
                    s.add(ExchangeRate(day=d, currency=cur, per_eur=rate, source="manual"))
    for provider, service, currency, amount in sources:
        lines = [
            CostLine(
                charge_date=d,
                provider=provider,
                service_name=service,
                sub_account_id="111",
                billed_cost=Decimal(amount),
                effective_cost=Decimal(amount),
                billing_currency=currency,
            )
            for d in days
        ]
        with db.session() as s:
            replace_billing_period(
                s,
                org_id=org_id,
                source_kind="t",
                source_key=f"t:{provider}",
                billing_period=first,
                lines=lines,
            )
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        s.add(
            CloudConnection(
                org_id=org_id,
                provider="gcp",
                name="GCP billing",
                status="error",
                external_id="g-1",
                last_error="BigQuery: permission denied",
            )
        )
    return days[-1]


def test_all_sources_side_by_side(client: TestClient, db: Database) -> None:
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    through = seed(db, org_id)
    c = sign_in_client(client, db, owner, org_id)
    out = c.get("/api/v1/costs/providers", params={"currency": "USD"}).json()
    n = through.day if through.day <= 3 else 3
    rows = {r["provider"]: r for r in out["providers"]}
    assert [r["provider"] for r in out["providers"]][:3] == ["aws", "azure", "snowflake"]
    assert rows["aws"]["month_to_date"] == 100 * n and rows["aws"]["name"] == "AWS"
    assert rows["snowflake"]["month_to_date"] == 12.5 * n  # converted from EUR
    assert out["month_to_date"] == (100 + 50 + 12.5) * n
    assert rows["aws"]["share_pct"] == round(100 / 162.5 * 100, 1)
    gcp = rows["gcp"]  # connected but no costs yet: still listed, with its problem
    assert gcp["name"] == "Google Cloud" and gcp["month_to_date"] == 0
    assert gcp["connections"][0]["status"] == "error" and gcp["data_through"] is None
    assert rows["azure"]["data_through"] == through.isoformat()


def test_provider_is_a_dimension(client: TestClient, db: Database) -> None:
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    through = seed(db, org_id)
    c = sign_in_client(client, db, owner, org_id)
    daily = c.get(
        "/api/v1/costs/daily",
        params={"dimension": "provider", "currency": "USD", "end": through.isoformat()},
    )
    assert daily.status_code == 200
    labels = {s["label"] for s in daily.json()["series"]}
    assert {"aws", "azure", "snowflake"} <= labels
    explored = c.post(
        "/api/v1/costs/explore",
        json={
            "start": through.replace(day=1).isoformat(),
            "end": through.isoformat(),
            "group_by": ["provider"],
            "granularity": "total",
            "currency": "USD",
        },
    ).json()
    assert {g["label"] for g in explored["groups"]} >= {"aws", "azure", "snowflake"}


def test_scoped_roles_cannot_see_totals(client: TestClient, db: Database) -> None:
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    dev = add_to_org(db, org_id, "dev@acme.com", Role.MEMBER)
    o = sign_in_client(client, db, owner, org_id)
    role = o.post(
        "/api/v1/org/custom-roles", json={"name": "Web", "permissions": [], "accounts": ["111"]}
    ).json()
    o.put(f"/api/v1/org/members/{dev}/custom-role", json={"custom_role_id": role["id"]})
    d = sign_in_client(client, db, dev, org_id)
    assert d.get("/api/v1/costs/providers").status_code == 403
