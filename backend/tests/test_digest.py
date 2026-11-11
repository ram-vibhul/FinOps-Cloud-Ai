from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from ccradar.costs import alerts, digest
from ccradar.costs.anomalies import detect
from ccradar.costs.ingest import CostLine, replace_billing_period
from ccradar.db import Database
from ccradar.db.tenant import set_tenant
from ccradar.models import Organization
from tests.helpers import make_org, sign_in_client

END = date(2026, 11, 8)  # Sunday


def load(db: Database, org_id) -> None:
    lines = []
    for i in range(35):
        day = END - timedelta(days=i)
        ec2 = 100 if i >= 7 else 160  # EC2 up in the last week; a spike on the last day
        if i == 0:
            ec2 = 400
        lines += [
            CostLine(
                charge_date=day,
                provider="aws",
                service_name=svc,
                billed_cost=Decimal(v),
                effective_cost=Decimal(v),
                billing_currency="USD",
            )
            for svc, v in (("Amazon EC2", ec2), ("Amazon S3", 20))
        ]
    with db.session() as s:
        for month in sorted({li.charge_date.replace(day=1) for li in lines}):
            replace_billing_period(
                s,
                org_id=org_id,
                source_kind="t",
                source_key="t",
                billing_period=month,
                lines=[li for li in lines if li.charge_date.replace(day=1) == month],
            )


def test_digest_numbers_channels_email_and_once_a_week(
    client: TestClient, db: Database, monkeypatch
) -> None:
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    load(db, org_id)
    c = sign_in_client(client, db, owner, org_id)
    assert (
        c.put("/api/v1/org/alert-settings", json={"weekly_digest_email": True}).status_code == 200
    )
    posted: list[tuple[str, list]] = []
    monkeypatch.setattr(
        digest, "notify", lambda db, event, messages: posted.append((event, list(messages))) or 1
    )
    queued: list[dict] = []
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        d = digest.build(s, org_id, date(2026, 11, 9))
        assert d is not None
        # Last 7 days: 6 x 180 + 420 = 1500; the 7 before: 7 x 120 = 840.
        assert d["start"] == "2026-11-02" and d["end"] == "2026-11-08"
        assert d["week"] == 1500 and d["previous"] == 840 and d["change_pct"] == 78.6
        assert d["movers"][0] == {"key": "Amazon EC2", "change": 660.0}
        text = digest.lines(d)
        assert text[0] == "Spend 2026-11-02 to 2026-11-08: 1,500.00 USD, up 79% on the week before."
        assert text[1].startswith("Moved most: Amazon EC2 (+660.00 USD)")
        n = digest.send(
            s,
            org_id,
            "https://ccradar.example",
            date(2026, 11, 9),
            lambda name, **kw: queued.append(kw),
        )
        again = digest.send(
            s,
            org_id,
            "https://ccradar.example",
            date(2026, 11, 11),
            lambda name, **kw: queued.append(kw),
        )
        next_week = digest.send(s, org_id, "https://ccradar.example", date(2026, 11, 16), None)
    assert n == 2 and again == 0 and next_week == 1  # channel + one email; then once a week
    event, messages = posted[0]
    assert event == "weekly_digest" and messages[0].title == "Acme: weekly cost digest"
    assert messages[0].link == "https://ccradar.example/costs" and messages[0].severity == "info"
    assert queued[0]["message"]["to"] == "o@acme.com"
    assert queued[0]["message"]["subject"] == "Acme: weekly cost digest"


def test_digest_skips_demo_and_empty_orgs(db: Database) -> None:
    org_id, _ = make_org(db, "Empty", "e@x.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        assert digest.send(s, org_id, "https://x", date(2026, 11, 9)) == 0
        org = s.get(Organization, org_id)
        assert org is not None
        org.is_demo = True
        assert digest.send(s, org_id, "https://x", date(2026, 11, 9)) == 0


def test_anomaly_alerts_also_go_to_channels(db: Database, monkeypatch) -> None:
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    load(db, org_id)
    posted: list[tuple[str, list]] = []
    monkeypatch.setattr(
        "ccradar.core.notify.notify",
        lambda db, event, messages: posted.append((event, list(messages))) or 1,
    )
    with db.session() as s:
        created = detect(s, org_id)
        assert created
        emails = alerts.pending_alert(s, org_id, "https://ccradar.example")
    assert emails and emails[0].to == "o@acme.com"
    event, messages = posted[0]
    assert event == "cost_anomaly" and messages[0].title == emails[0].subject
    assert "Amazon EC2" in messages[0].text and messages[0].link == "https://ccradar.example/costs"
