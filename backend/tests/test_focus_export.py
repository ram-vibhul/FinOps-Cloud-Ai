from __future__ import annotations

import csv
import io
import json
from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from ccradar.costs import focus_export
from ccradar.costs.ingest import CostLine, replace_billing_period
from ccradar.db import Database
from tests.helpers import make_org, sign_in_client


def test_focus_csv_has_focus_columns_and_exact_amounts(
    client: TestClient, db: Database, monkeypatch
) -> None:
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    other_id, _ = make_org(db, "Other", "x@other.com")
    lines = [
        CostLine(
            charge_date=date(2026, 10, d),
            provider="aws",
            billing_account_id="999",
            sub_account_id="111",
            service_name="Amazon EC2",
            service_category="Compute",
            region="eu-west-1",
            resource_id=f"i-{d}",
            usage_type="EU-BoxUsage:m7g.large",
            charge_category="Usage",
            pricing_category="Standard",
            consumed_quantity=Decimal("24"),
            consumed_unit="Hrs",
            billed_cost=Decimal("1.234567"),
            effective_cost=Decimal("1.1"),
            billing_currency="USD",
            tags={"team": "payments", "env": "prod"},
        )
        for d in range(1, 8)
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
        replace_billing_period(
            s,
            org_id=other_id,
            source_kind="t",
            source_key="t",
            billing_period=date(2026, 10, 1),
            lines=lines[:2],
        )
    monkeypatch.setattr(focus_export, "BATCH", 3)  # several chunks
    c = sign_in_client(client, db, owner, org_id)
    r = c.get("/api/v1/statements/2026-10/focus.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert 'filename="focus-2026-10.csv"' in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert list(rows[0]) == focus_export.COLUMNS
    assert len(rows) == 7  # only this organisation's rows
    first = rows[0]
    assert first["ProviderName"] == "AWS" and first["BillingAccountId"] == "999"
    assert first["ChargePeriodStart"] == "2026-10-01T00:00:00Z"
    assert first["ChargePeriodEnd"] == "2026-10-02T00:00:00Z"
    assert first["BillingPeriodStart"] == "2026-10-01T00:00:00Z"
    assert first["BillingPeriodEnd"] == "2026-11-01T00:00:00Z"
    assert first["ServiceCategory"] == "Compute" and first["RegionId"] == "eu-west-1"
    assert json.loads(first["Tags"]) == {"env": "prod", "team": "payments"}
    assert first["x_UsageType"] == "EU-BoxUsage:m7g.large" and first["ListCost"] == ""
    # Amounts are written exactly, so the export adds up to the bill.
    assert sum(Decimal(x["BilledCost"]) for x in rows) == Decimal("1.234567") * 7
    empty = c.get("/api/v1/statements/2026-09/focus.csv").text
    assert empty.strip() == ",".join(focus_export.COLUMNS)
    assert c.get("/api/v1/statements/2026-13/focus.csv").status_code == 422
