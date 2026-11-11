from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from ccradar.accounts.roles import Role
from ccradar.costs import fx
from ccradar.costs.ingest import CostLine, replace_billing_period
from ccradar.db import Database
from ccradar.db.tenant import set_tenant
from tests.helpers import add_to_org, make_org, sign_in_client

NOV = date(2026, 11, 1)


def seed(db: Database, org_id) -> None:
    with db.session() as s:
        set_tenant(s, bypass=True)
        # Reference: 1 EUR = 1.10 USD = 92.40 INR  ->  1 USD = 84 INR
        fx.store(
            s,
            {NOV: {"EUR": Decimal(1), "USD": Decimal("1.10"), "INR": Decimal("92.40")}},
            until=date(2026, 11, 3),
        )
    with db.session() as s:
        replace_billing_period(
            s,
            org_id=org_id,
            source_kind="t",
            source_key="t",
            billing_period=NOV,
            lines=[
                CostLine(
                    charge_date=date(2026, 11, d),
                    provider="aws",
                    service_name="EC2",
                    billed_cost=Decimal(100),
                    effective_cost=Decimal(100),
                    billing_currency="USD",
                )
                for d in (1, 2, 3)
            ],
        )


def total_in_inr(c: TestClient) -> float:
    r = c.post(
        "/api/v1/costs/explore",
        json={"start": "2026-11-01", "end": "2026-11-03", "currency": "INR", "group_by": []},
    )
    assert r.status_code == 200, r.text
    return r.json()["total"]


def test_override_wins_for_its_days_both_directions_and_only_for_its_org(
    client: TestClient, db: Database
) -> None:
    org_id, owner = make_org(db, "Acme", "owner@acme.com")
    other_id, other_owner = make_org(db, "Other", "o@other.com")
    viewer = add_to_org(db, org_id, "viewer@acme.com", Role.VIEWER)
    seed(db, org_id)
    seed_other = other_id
    with db.session() as s:
        replace_billing_period(
            s,
            org_id=seed_other,
            source_kind="t",
            source_key="t",
            billing_period=NOV,
            lines=[
                CostLine(
                    charge_date=date(2026, 11, 1),
                    provider="aws",
                    service_name="EC2",
                    billed_cost=Decimal(100),
                    effective_cost=Decimal(100),
                    billing_currency="USD",
                )
            ],
        )
    c = sign_in_client(client, db, owner, org_id)
    assert round(total_in_inr(c), 2) == 25200.0  # 300 USD x 84
    body = {
        "from_currency": "INR",  # entered the "other way round": 1 INR = 0.0125 USD -> 80 INR/USD
        "to_currency": "USD",
        "start_day": "2026-11-02",
        "end_day": "2026-11-30",
        "rate": "0.0125",
        "note": "Finance rate November",
    }
    v = sign_in_client(client, db, viewer, org_id)
    assert v.post("/api/v1/fx/overrides", json=body).status_code == 403
    c = sign_in_client(client, db, owner, org_id)
    created = c.post("/api/v1/fx/overrides", json=body)
    assert created.status_code == 201, created.text
    assert round(total_in_inr(c), 2) == 8400.0 + 2 * 8000.0  # day 1 reference, days 2-3 override
    assert (
        c.post("/api/v1/fx/overrides", json={**body, "start_day": "2026-11-20"}).status_code == 409
    )
    rate = c.get(
        "/api/v1/fx/rate", params={"day": "2026-11-02", "source": "USD", "target": "INR"}
    ).json()
    assert (
        rate["override"] is True
        and round(rate["rate"], 4) == 80
        and round(rate["reference_rate"], 4) == 84
    )
    # The other organisation still uses the reference rate and cannot see the override.
    o = sign_in_client(client, db, other_owner, other_id)
    assert o.get("/api/v1/fx/overrides").json() == []
    assert round(total_in_inr(o), 2) == 8400.0
    oid = created.json()["id"]
    assert o.delete(f"/api/v1/fx/overrides/{oid}").status_code == 404
    c = sign_in_client(client, db, owner, org_id)
    assert c.delete(f"/api/v1/fx/overrides/{oid}").status_code == 204
    assert round(total_in_inr(c), 2) == 25200.0


def test_validation(client: TestClient, db: Database) -> None:
    org_id, owner = make_org(db, "Acme", "owner@acme.com")
    c = sign_in_client(client, db, owner, org_id)
    base = {
        "from_currency": "USD",
        "to_currency": "INR",
        "start_day": "2026-11-01",
        "end_day": "2026-11-30",
        "rate": "84",
    }
    assert c.post("/api/v1/fx/overrides", json={**base, "to_currency": "USD"}).status_code == 422
    assert c.post("/api/v1/fx/overrides", json={**base, "end_day": "2026-10-01"}).status_code == 422
    assert c.post("/api/v1/fx/overrides", json={**base, "rate": "0"}).status_code == 422
    assert c.post("/api/v1/fx/overrides", json={**base, "from_currency": "usd"}).status_code == 422
