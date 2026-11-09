from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from costlens.accounts.roles import Role
from costlens.costs.ingest import CostLine, replace_billing_period
from costlens.db import Database
from costlens.db.tenant import set_tenant
from costlens.models import CloudConnection
from tests.helpers import add_to_org, make_org, sign_in_client


def test_checklist_follows_what_was_done(client: TestClient, db: Database) -> None:
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    c = sign_in_client(client, db, owner, org_id)
    first = c.get("/api/v1/org/setup").json()
    assert first["done"] == 0 and first["total"] == 6 and first["next"] == "connect_cloud"
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        s.add(
            CloudConnection(
                org_id=org_id, provider="aws", name="Prod", status="active", external_id="x"
            )
        )
        replace_billing_period(
            s, org_id=org_id, source_kind="t", source_key="t", billing_period=date(2026, 10, 1),
            lines=[CostLine(charge_date=date(2026, 10, 1), provider="aws", service_name="Amazon EC2", billed_cost=Decimal(1), effective_cost=Decimal(1), billing_currency="USD")],
        )  # fmt: skip
    add_to_org(db, org_id, "dev@acme.com", Role.MEMBER)
    c = sign_in_client(client, db, owner, org_id)
    c.post("/api/v1/budgets", json={"name": "All", "amount": 1000})
    out = c.get("/api/v1/org/setup").json()
    done = {s["key"] for s in out["steps"] if s["done"]}
    assert done == {"connect_cloud", "first_costs", "invite", "budget"}
    assert out["next"] == "review_savings" and out["done"] == 4
    assert c.post("/api/v1/org/setup/hide").status_code == 204
    assert c.get("/api/v1/org/setup").json()["hidden"] is True
