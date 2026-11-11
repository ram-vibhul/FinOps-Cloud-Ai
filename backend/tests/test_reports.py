from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select

from ccradar.core.email import Email, SmtpSender
from ccradar.costs.ingest import CostLine, replace_billing_period
from ccradar.db import Database
from ccradar.db.base import utcnow
from ccradar.db.tenant import set_tenant
from ccradar.models import ReportSchedule
from ccradar.reports import scheduled
from tests.helpers import make_org, sign_in_client


def test_email_attachments_are_built() -> None:
    sender = SmtpSender(
        host="smtp", port=25, username=None, password=None, security="none", from_address="c@x.io"
    )
    mime = sender.build(
        Email(
            to="a@b.co",
            subject="s",
            text="t",
            attachments=[
                {
                    "filename": "r.pdf",
                    "content_type": "application/pdf",
                    "data": base64.b64encode(b"%PDF-1").decode(),
                }
            ],
        )
    )
    parts = [p for p in mime.iter_attachments()]
    assert parts[0].get_filename() == "r.pdf" and parts[0].get_content() == b"%PDF-1"


def test_due_rules() -> None:
    s = ReportSchedule(
        name="x",
        kind="savings",
        frequency="weekly",
        weekday=0,
        day=1,
        recipients=["a@b.co"],
        enabled=True,
    )
    monday = datetime(2026, 11, 9, 8, tzinfo=UTC)
    assert scheduled.is_due(s, monday) and not scheduled.is_due(s, monday.replace(hour=6))
    assert not scheduled.is_due(s, monday + timedelta(days=1))
    s.last_sent_at = monday.replace(hour=7)
    assert not scheduled.is_due(s, monday)
    s.frequency, s.day, s.last_sent_at = "monthly", 9, None
    assert scheduled.is_due(s, monday) and not scheduled.is_due(s, monday + timedelta(days=1))


def test_schedules_send_and_download(client: TestClient, db: Database) -> None:
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    today = utcnow().date()
    last = today.replace(day=1) - timedelta(days=1)
    lines = [
        CostLine(
            charge_date=last - timedelta(days=d),
            provider="aws",
            service_name="Amazon EC2",
            sub_account_id="111",
            billed_cost=Decimal(10),
            effective_cost=Decimal(10),
            billing_currency="USD",
        )
        for d in range(5)
    ]
    with db.session() as s:
        replace_billing_period(
            s,
            org_id=org_id,
            source_kind="t",
            source_key="t",
            billing_period=last.replace(day=1),
            lines=lines,
        )
    c = sign_in_client(client, db, owner, org_id)
    assert (
        c.post(
            "/api/v1/reports/schedules",
            json={"name": "x", "kind": "savings", "recipients": ["nope"]},
        ).status_code
        == 422
    )
    r = c.post(
        "/api/v1/reports/schedules",
        json={"name": "Monthly costs", "kind": "cost_summary", "recipients": ["cfo@acme.com"]},
    )
    assert r.status_code == 201
    pdf = c.get("/api/v1/reports/download", params={"kind": "cost_summary"})
    assert pdf.content.startswith(b"%PDF") and pdf.headers["content-type"] == "application/pdf"
    csv_text = c.get(
        "/api/v1/reports/download", params={"kind": "cost_summary", "format": "csv"}
    ).text
    assert "Amazon EC2,50.00 USD" in csv_text
    for kind in ("savings", "budgets", "showback"):
        assert c.get("/api/v1/reports/download", params={"kind": kind}).content.startswith(
            b"%PDF"
        ), kind
    delivered: list[dict] = []
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        sched = s.scalars(select(ReportSchedule)).one()
        sched.day = min(today.day, 28)  # the UTC day, like `now` below
        now = datetime.combine(today, datetime.min.time(), UTC).replace(
            hour=9, day=min(today.day, 28)
        )
        sent = scheduled.send_due(s, org_id, delivered.append, now=now)
        again = scheduled.send_due(s, org_id, delivered.append, now=now)
    assert sent == 1 and again == 0
    mail = delivered[0]
    assert mail["to"] == "cfo@acme.com" and mail["subject"] == "CloudCostRadar: Monthly costs"
    assert mail["attachments"][0]["filename"].endswith(".pdf") and "total 50.00 USD" in mail["text"]
