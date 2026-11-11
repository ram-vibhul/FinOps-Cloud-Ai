from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from ccradar.costs.ingest import CostLine, replace_billing_period
from ccradar.db import Database
from tests.helpers import make_org, sign_in_client


def test_cost_per_unit(client: TestClient, db: Database) -> None:
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    lines = [
        CostLine(
            charge_date=date(2026, 10, d),
            provider="aws",
            service_name="Amazon EC2",
            billed_cost=Decimal(c),
            effective_cost=Decimal(c),
            billing_currency="USD",
            tags=tags,
        )
        for d in range(1, 11)
        for c, tags in (("100", {"product": "checkout"}), ("40", {"product": "search"}))
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
    c = sign_in_client(client, db, owner, org_id)
    assert (
        c.post(
            "/api/v1/unit-metrics", json={"name": "x", "unit": "y", "filters": {"bogus": ["a"]}}
        ).status_code
        == 422
    )
    m = c.post(
        "/api/v1/unit-metrics",
        json={
            "name": "Orders",
            "unit": "order",
            "filters": {"tag:product": ["checkout"]},
            "target_per_unit": 0.05,
        },
    ).json()
    # Orders: 1,000 a day for the first 5 days, then 2,500 a day (scale makes each order cheaper).
    points = [{"day": f"2026-10-{d:02d}", "value": 1000 if d <= 5 else 2500} for d in range(1, 11)]
    assert c.post(f"/api/v1/unit-metrics/{m['id']}/values", json=points).json() == {"stored": 10}
    csv_body = "day,value\n2026-10-10,2000\n"
    r = c.post(
        f"/api/v1/unit-metrics/{m['id']}/values.csv",
        content=csv_body,
        headers={"Content-Type": "text/csv"},
    )
    assert r.json() == {"stored": 1}  # replaces the value for the 10th
    bad = c.post(
        f"/api/v1/unit-metrics/{m['id']}/values.csv",
        content="2026-13-01,5\n",
        headers={"Content-Type": "text/csv"},
    )
    assert bad.json()["code"] == "bad_csv"
    rep = c.get(
        f"/api/v1/unit-metrics/{m['id']}/report",
        params={"start": "2026-10-01", "end": "2026-10-10"},
    ).json()
    units = 5 * 1000 + 4 * 2500 + 2000
    assert rep["per_unit"] == pytest.approx(1000 / units, abs=1e-6)  # only checkout cost counts
    assert rep["days"][0]["per_unit"] == pytest.approx(0.1) and rep["days"][9][
        "per_unit"
    ] == pytest.approx(0.05)
    assert rep["change_pct"] == pytest.approx(round(100 * ((500 / 12000) - 0.1) / 0.1, 1), abs=0.1)
    assert rep["months"][0]["month"] == "2026-10" and rep["metric"]["target_per_unit"] == 0.05
