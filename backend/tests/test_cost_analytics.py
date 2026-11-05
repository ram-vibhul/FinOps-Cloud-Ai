from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from costlens.costs import analytics
from costlens.costs.ingest import CostLine, replace_billing_period
from costlens.db import Database
from costlens.db.tenant import set_tenant
from tests.helpers import make_org, sign_in_client

TODAY = date(2026, 11, 15)


def L(
    day: date,
    cost: float,
    service: str = "EC2",
    account: str = "1",
    region: str = "ap-south-1",
    category: str = "Usage",
    currency: str = "USD",
) -> CostLine:
    return CostLine(
        charge_date=day,
        provider="aws",
        service_name=service,
        sub_account_id=account,
        region=region,
        billed_cost=Decimal(str(cost)),
        effective_cost=Decimal(str(cost)),
        billing_currency=currency,
        charge_category=category,
        pricing_category=None if category in ("Tax", "Credit") else "Standard",
    )


def load(db: Database, org_id, lines: list[CostLine]) -> None:
    by_month: dict[date, list[CostLine]] = {}
    for line in lines:
        by_month.setdefault(line.charge_date.replace(day=1), []).append(line)
    with db.session() as s:
        for month, items in by_month.items():
            replace_billing_period(
                s,
                org_id=org_id,
                source_kind="test",
                source_key="t",
                billing_period=month,
                lines=items,
            )


@pytest.fixture
def org(db: Database):
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    lines = []
    start = date(2026, 9, 1)
    for i in range((TODAY - start).days):  # data through Nov 14 (one-day lag)
        day = start + timedelta(days=i)
        weekend = day.weekday() >= 5
        lines.append(L(day, 70 if weekend else 100, "EC2"))
        lines.append(L(day, 20, "S3", account="2"))
        if day >= date(2026, 11, 8):
            lines.append(L(day, 30, "Redshift", account="2"))  # new service last week
        if day.day == 1:
            lines.append(L(day, 500, "Savings Plans", category="Purchase"))
        if day == date(2026, 10, 31):
            lines.append(L(day, 90, "Tax", category="Tax"))
    lines.append(L(date(2026, 11, 3), 40, "VM", currency="EUR"))
    load(db, org_id, lines)
    return {"org": org_id, "owner": owner, "db": db}


def test_forecast(org) -> None:
    with org["db"].session() as s:
        set_tenant(s, org_id=org["org"])
        fc = analytics.forecast_month(s, org["org"], "USD", "effective", TODAY)
    assert fc.data_through == date(2026, 11, 14)
    # MTD: 14 days of EC2 (10 weekdays*100 + 4 weekend*70) + S3 14*20 + Redshift 7*30 + SP 500
    assert fc.month_to_date == pytest.approx(1000 + 280 + 280 + 210 + 500)
    assert fc.recurring_expected == pytest.approx(90)  # last month's tax on the 31st/30th
    # 16 remaining days at roughly 100-125/day + tax, inside a sensible range
    assert 2270 + 1500 < fc.forecast < 2270 + 2400
    assert fc.low <= fc.forecast <= fc.high
    assert fc.low >= fc.month_to_date


def test_forecast_without_data(db: Database) -> None:
    org_id, _ = make_org(db, "Empty", "e@x.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        fc = analytics.forecast_month(s, org_id, "USD", "effective", TODAY)
    assert fc.method == "no data" and fc.forecast == 0


def test_summary_api(client: TestClient, org, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("costlens.api.costs._today", lambda: TODAY)
    c = sign_in_client(client, org["db"], org["owner"], org["org"])
    s = c.get("/api/v1/costs/summary").json()
    assert s["currency"] == "USD" and s["data_through"] == "2026-11-14"
    assert s["month_to_date"] == pytest.approx(2270)
    assert s["other_currencies"] == {"EUR": 40.0}
    assert s["previous_month_to_date"] > 0 and s["change_vs_previous_month_pct"] is not None
    assert s["forecast"]["amount"] > s["month_to_date"]
    eur = c.get("/api/v1/costs/summary", params={"currency": "EUR"}).json()
    assert eur["month_to_date"] == 40.0
    assert c.get("/api/v1/costs/summary", params={"currency": "usd"}).status_code == 422


def test_daily_series_with_other(client: TestClient, org) -> None:
    c = sign_in_client(client, org["db"], org["owner"], org["org"])
    r = c.get(
        "/api/v1/costs/daily",
        params={"dimension": "service", "top": 2, "start": "2026-11-01", "end": "2026-11-14"},
    ).json()
    assert len(r["periods"]) == 14 and r["periods"][0] == "2026-11-01"
    labels = [x["label"] for x in r["series"]]
    assert labels[:2] == ["EC2", "Savings Plans"] and labels[-1].startswith("Other")
    assert sum(x["total"] for x in r["series"]) == pytest.approx(r["total"])
    ec2 = next(x for x in r["series"] if x["key"] == "EC2")
    assert ec2["values"][0] == 70.0  # Nov 1 2026 is a Sunday


def test_monthly_series(client: TestClient, org, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("costlens.api.costs._today", lambda: TODAY)
    c = sign_in_client(client, org["db"], org["owner"], org["org"])
    r = c.get("/api/v1/costs/monthly", params={"months": 3}).json()
    assert r["periods"] == ["2026-09", "2026-10", "2026-11"]
    total = r["series"][0]["values"]
    assert total[2] == pytest.approx(2270) and total[1] > total[0]


def test_movers(client: TestClient, org) -> None:
    c = sign_in_client(client, org["db"], org["owner"], org["org"])
    r = c.get("/api/v1/costs/movers", params={"days": 7, "dimension": "service"}).json()
    assert r["current_period"] == ["2026-11-08", "2026-11-14"]
    top = r["items"][0]
    assert top["key"] == "Redshift" and top["status"] == "new" and top["change"] == 210.0
    with_purchases = c.get(
        "/api/v1/costs/movers", params={"days": 7, "include_purchases": True}
    ).json()
    assert with_purchases["items"][0]["key"] == "Savings Plans"  # paid Nov 1, in the old window
    by_account = c.get("/api/v1/costs/movers", params={"days": 7, "dimension": "account"}).json()
    assert by_account["items"][0]["key"] == "2"


def test_billed_vs_effective_and_isolation(client: TestClient, org, db: Database) -> None:
    other, boss = make_org(db, "Other", "b@other.com")
    c = sign_in_client(client, db, boss, other)
    assert c.get("/api/v1/costs/daily").json()["total"] == 0
    assert (
        c.get(
            "/api/v1/costs/daily", params={"start": "2026-01-01", "end": "2027-12-31"}
        ).status_code
        == 400
    )
