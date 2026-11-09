from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from costlens.accounts.roles import Role
from costlens.costs.ingest import CostLine, replace_billing_period
from costlens.db import Database
from costlens.db.tenant import set_tenant
from costlens.models import CloudConnection, CostImportRun
from tests.fakes.aws import FakeAws
from tests.helpers import add_to_org, make_org, sign_in_client

OCT = date(2026, 10, 1)
ROLE = "arn:aws:iam::111111111111:role/costlens"


def _lines(cost: str) -> list[CostLine]:
    return [
        CostLine(
            charge_date=date(2026, 10, d),
            provider="aws",
            service_name=svc,
            sub_account_id="111",
            billing_account_id="999",
            charge_category=cat,
            billed_cost=Decimal(c),
            effective_cost=Decimal(c),
            billing_currency="USD",
        )
        for d in range(1, 11)
        for svc, cat, c in (("Amazon EC2", "Usage", cost), ("Tax", "Tax", "2"))
    ]


class FakeInvoicing:
    def list_invoice_summaries(self, **kw: Any) -> dict[str, Any]:
        assert kw["Filter"] == {"BillingPeriod": {"Month": 10, "Year": 2026}}
        return {
            "InvoiceSummaries": [
                {
                    "InvoiceId": "EUINGB26-1",
                    "AccountId": "999",
                    "BaseCurrencyAmount": {"TotalAmount": "1020.00", "CurrencyCode": "USD"},
                }
            ]
        }


def test_statement_reconcile_close_and_lock(client: TestClient, db: Database) -> None:
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    with db.session() as s:
        replace_billing_period(
            s,
            org_id=org_id,
            source_kind="t",
            source_key="t",
            billing_period=OCT,
            lines=_lines("100"),
        )
        set_tenant(s, org_id=org_id)
        s.add(
            CloudConnection(
                org_id=org_id,
                provider="aws",
                name="Payer",
                status="active",
                external_id="x",
                role_arn=ROLE,
                account_id="999",
            )
        )
    member = add_to_org(db, org_id, "m@acme.com", Role.MEMBER)
    m = sign_in_client(client, db, member, org_id)
    st = m.get("/api/v1/statements/2026-10").json()
    assert st["total"] == 1020 and st["closed"] is None
    assert {c["key"]: c["cost"] for c in st["by_category"]} == {"Usage": 1000, "Tax": 20}
    assert m.post("/api/v1/statements/2026-10/close", json={}).status_code == 403
    o = sign_in_client(client, db, owner, org_id)
    r = o.post(
        "/api/v1/statements/2026-10/invoices",
        json={"invoice_id": "INV-1", "amount": 1100, "billing_account_id": "999"},
    )
    assert (
        r.status_code == 201
        and r.json()[0]["status"] == "mismatch"
        and r.json()[0]["difference"] == 80
    )
    client.app.state.aws = FakeAws(clients={"invoicing": FakeInvoicing()})  # type: ignore[attr-defined]
    client.app.state.aws.allow(ROLE, "x")  # type: ignore[attr-defined]
    fetched = o.post("/api/v1/statements/2026-10/invoices/fetch").json()
    assert fetched["added"] == 1
    status = {i["invoice_id"]: i["status"] for i in fetched["invoices"]}
    assert status == {"INV-1": "mismatch", "EUINGB26-1": "matched"}
    assert o.post("/api/v1/statements/2026-10/invoices/fetch").json()["added"] == 0  # no duplicates
    closed = o.post("/api/v1/statements/2026-10/close", json={"note": "Finance signed off"}).json()
    assert closed["closed"]["note"] == "Finance signed off" and closed["total"] == 1020
    assert o.post("/api/v1/statements/2026-10/close", json={}).json()["code"] == "already_closed"
    # A late re-import is skipped: the closed month keeps its numbers.
    with db.session() as s:
        run = replace_billing_period(
            s,
            org_id=org_id,
            source_kind="t",
            source_key="t",
            billing_period=OCT,
            lines=_lines("500"),
        )
        assert run.status == "skipped_closed"
    assert o.get("/api/v1/statements/2026-10").json()["total"] == 1020
    csv_text = o.get("/api/v1/statements/2026-10/statement.csv").text
    assert "category,Usage,1000.00,USD" in csv_text and "invoice,EUINGB26-1,1020.00,USD" in csv_text
    assert o.delete("/api/v1/statements/2026-10/close").json()["closed"] is None
    with db.session() as s:
        run = replace_billing_period(
            s,
            org_id=org_id,
            source_kind="t",
            source_key="t",
            billing_period=OCT,
            lines=_lines("500"),
        )
        assert run.status == "succeeded"
        set_tenant(s, org_id=org_id)
        assert s.scalars(
            select(CostImportRun).where(CostImportRun.status == "skipped_closed")
        ).one()
    assert o.get("/api/v1/statements/2026-10").json()["total"] == 5020
    assert o.post("/api/v1/statements/2099-01/close", json={}).json()["code"] == "month_not_over"
