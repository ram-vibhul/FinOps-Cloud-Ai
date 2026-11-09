from __future__ import annotations

import re
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from costlens.costs.ingest import CostLine, replace_billing_period
from costlens.db import Database
from costlens.db.tenant import set_tenant
from costlens.models import ReportSchedule
from costlens.reports import leadership, scheduled
from tests.helpers import make_org, sign_in_client


def load_month(db: Database, org_id, month: date, costs: dict[str, str]) -> None:
    lines = [
        CostLine(
            charge_date=month.replace(day=d),
            provider="aws",
            service_name=svc,
            billed_cost=Decimal(cost),
            effective_cost=Decimal(cost),
            billing_currency="USD",
        )
        for d in range(1, 11)
        for svc, cost in costs.items()
    ]
    with db.session() as s:
        replace_billing_period(
            s, org_id=org_id, source_kind="t", source_key="t", billing_period=month, lines=lines
        )


def pages(pdf: bytes) -> int:
    return len(re.findall(rb"/Type\s*/Page[^s]", pdf))


@pytest.fixture
def acme(client: TestClient, db: Database):
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    # September: EC2 50/day, S3 20/day. October: EC2 80/day, S3 10/day, RDS 5/day.
    load_month(db, org_id, date(2026, 9, 1), {"Amazon EC2": "50", "Amazon S3": "20"})
    load_month(
        db, org_id, date(2026, 10, 1), {"Amazon EC2": "80", "Amazon S3": "10", "Amazon RDS": "5"}
    )
    c = sign_in_client(client, db, owner, org_id)
    for name, amount, filters in (
        ("EC2", 600, {"service": ["Amazon EC2"]}),
        ("Everything", 2000, {}),
    ):
        r = c.post(
            "/api/v1/budgets",
            json={"name": name, "amount": amount, "filters": filters, "alert_on_forecast": False},
        )
        assert r.status_code == 201, r.text
    return org_id, c


def test_numbers_and_sentences(acme) -> None:
    _, c = acme
    d = c.get("/api/v1/reports/leadership", params={"month": "2026-10"}).json()
    assert d["month_label"] == "October 2026" and d["total"] == 950 and d["previous_total"] == 700
    assert d["change_pct"] == pytest.approx(35.7)
    assert d["grew"] == [
        {"key": "Amazon EC2", "change": 300.0},
        {"key": "Amazon RDS", "change": 50.0},
    ]
    assert d["shrank"] == [{"key": "Amazon S3", "change": -100.0}]
    assert [b["name"] for b in d["budgets_over"]] == ["EC2"]  # 800 of 600
    assert d["sentences"][0] == (
        "Cloud spend in October 2026 was 950 USD, up 36% from 700 USD the month before."
    )
    assert "Amazon EC2 (+300 USD)" in d["sentences"][1]
    assert d["sentences"][2] == "1 of 2 monthly budgets went over: EC2 (133%)."
    text = leadership.to_text(d)
    assert "Total: 950 USD (previous month 700 USD)" in text and "PDF is attached" in text
    assert c.get("/api/v1/reports/leadership", params={"month": "2026-13"}).status_code == 422


def test_pdf_is_one_page_even_with_long_lists(acme, db: Database) -> None:
    org_id, _ = acme
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        d = leadership.build(s, org_id, date(2026, 10, 1))
    pdf = leadership.to_pdf(d)
    assert pdf.startswith(b"%PDF") and pages(pdf) == 1
    d["budgets"] = [
        {"name": f"Team {i} " * 6, "amount": 1, "spent": 2, "currency": "USD", "pct": 200 + i}
        for i in range(40)
    ]
    d["sentences"] = d["sentences"] * 12
    assert pages(leadership.to_pdf(d)) == 1


def test_scheduled_leadership_email(acme, db: Database, monkeypatch) -> None:
    org_id, c = acme
    monkeypatch.setattr(leadership, "utcnow", lambda: datetime(2026, 11, 3, 9, tzinfo=UTC))
    r = c.post(
        "/api/v1/reports/schedules",
        json={"name": "Board pack", "kind": "leadership", "day": 3, "recipients": ["ceo@acme.com"]},
    )
    assert r.status_code == 201 and r.json()["kind_label"] == "Leadership one-pager"
    csv_text = c.get(
        "/api/v1/reports/download", params={"kind": "leadership", "format": "csv"}
    ).text
    assert "Amazon EC2,300.00 USD" in csv_text
    delivered: list[dict] = []
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        sched = s.scalars(select(ReportSchedule)).one()
        scheduled.send(s, org_id, sched, delivered.append)
    mail = delivered[0]
    assert mail["to"] == "ceo@acme.com" and mail["subject"] == "CostLens: Board pack"
    assert mail["text"].startswith("CostLens leadership report — October 2026")
    assert "up 36%" in mail["text"]
    att = mail["attachments"][0]
    assert att["filename"] == "costlens-leadership-2026-10.pdf"
