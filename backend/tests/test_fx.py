from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from ccradar.costs import fx
from ccradar.costs.ingest import CostLine, replace_billing_period
from ccradar.db import Database
from ccradar.db.tenant import set_tenant
from ccradar.models import ExchangeRate, Organization
from tests.helpers import make_org, sign_in_client

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01" xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
<Cube>
<Cube time='2026-11-06'><Cube currency='USD' rate='1.10'/><Cube currency='INR' rate='92.40'/></Cube>
<Cube time='2026-11-05'><Cube currency='USD' rate='1.08'/><Cube currency='INR' rate='91.80'/></Cube>
</Cube></gesmes:Envelope>"""


def test_parse_and_fill_forward(db: Database) -> None:
    parsed = fx.parse_ecb(FEED)
    assert parsed[date(2026, 11, 5)]["USD"] == Decimal("1.08")
    assert parsed[date(2026, 11, 5)]["EUR"] == 1
    with db.session() as s:
        rows = fx.store(s, parsed, until=date(2026, 11, 8))  # Sat 7 and Sun 8 carried
        assert rows == 4 * 3
        assert fx.rate(s, date(2026, 11, 8), "USD", "INR") == Decimal("92.40") / Decimal("1.10")
        assert fx.rate(s, date(2026, 11, 7), "EUR", "EUR") == 1
        assert fx.rate(s, date(2026, 10, 1), "USD", "INR") is None
        carried = s.scalar(select(ExchangeRate.source).where(ExchangeRate.day == date(2026, 11, 8)))
        assert carried == "carried"


def test_parse_ignores_garbage() -> None:
    assert fx.parse_ecb("<!DOCTYPE x [<!ENTITY a 'b'>]><x>&a;</x>") == {}


def test_refresh_uses_history_first_then_90_days(db: Database) -> None:
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(200, text=FEED)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with db.session() as s:
        assert fx.refresh(s, client, today=date(2026, 11, 6)) > 0
    with db.session() as s:
        fx.refresh(s, client, today=date(2026, 11, 6))
        assert fx.refresh(s, client, today=date(2026, 11, 6), provider="none") == 0
    assert urls == [fx.ECB_HISTORY, fx.ECB_90D]


def test_refresh_survives_outage(db: Database) -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    with db.session() as s:
        assert fx.refresh(s, client, today=date(2026, 11, 6)) == 0


@pytest.fixture
def mixed(client: TestClient, db: Database):
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        org = s.get(Organization, org_id)
        assert org is not None
        org.default_currency = "INR"
    days = [date(2026, 11, 1) + timedelta(days=i) for i in range(6)]
    with db.session() as s:
        fx.store(
            s,
            {days[0]: {"EUR": Decimal(1), "USD": Decimal("1.0"), "INR": Decimal("90")}},
            until=days[-1],
        )
    lines = []
    for d in days:
        lines.append(
            CostLine(
                charge_date=d,
                provider="aws",
                service_name="EC2",
                billed_cost=Decimal(10),
                effective_cost=Decimal(10),
                billing_currency="USD",
            )
        )
        lines.append(
            CostLine(
                charge_date=d,
                provider="azure",
                service_name="VMs",
                billed_cost=Decimal(450),
                effective_cost=Decimal(450),
                billing_currency="INR",
            )
        )
        lines.append(
            CostLine(
                charge_date=d,
                provider="gcp",
                service_name="GCE",
                billed_cost=Decimal(5),
                effective_cost=Decimal(5),
                billing_currency="JPY",
            )
        )  # no rate
    with db.session() as s:
        replace_billing_period(
            s, org_id=org_id, source_kind="t", source_key="t", billing_period=days[0], lines=lines
        )
    return {"c": client, "db": db, "org": org_id, "owner": owner}


def test_spend_converted_to_display_currency(mixed) -> None:
    c = sign_in_client(mixed["c"], mixed["db"], mixed["owner"], mixed["org"])
    r = c.post(
        "/api/v1/costs/explore",
        json={
            "start": "2026-11-01",
            "end": "2026-11-06",
            "group_by": ["service"],
            "granularity": "total",
        },
    ).json()
    assert r["currency"] == "INR"
    totals = {g["label"]: g["total"] for g in r["groups"]}
    assert totals == {"EC2": 5400.0, "VMs": 2700.0}  # 6 x 10 USD x 90 ; JPY not convertible
    usd = c.post(
        "/api/v1/costs/explore",
        json={
            "start": "2026-11-01",
            "end": "2026-11-06",
            "group_by": ["service"],
            "granularity": "total",
            "currency": "USD",
        },
    ).json()
    assert {g["label"]: g["total"] for g in usd["groups"]} == {"EC2": 60.0, "VMs": 30.0}


def test_summary_reports_unconverted(mixed, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ccradar.api.costs._today", lambda: date(2026, 11, 7))
    c = sign_in_client(mixed["c"], mixed["db"], mixed["owner"], mixed["org"])
    s = c.get("/api/v1/costs/summary").json()
    assert s["currency"] == "INR" and s["month_to_date"] == 8100.0
    assert s["other_currencies"] == {"JPY": 30.0}


def test_rates_are_global_not_tenant_data(mixed) -> None:
    with mixed["db"].session() as s:
        assert s.scalar(select(func.count()).select_from(ExchangeRate)) == 6 * 3
