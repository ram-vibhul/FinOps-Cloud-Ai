from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from costlens.accounts.roles import Role
from costlens.core.email import MemorySender
from costlens.costs import anomalies
from costlens.costs.alerts import pending_alert
from costlens.costs.anomalies import AlertSettings, detect
from costlens.costs.ingest import CostLine, replace_billing_period
from costlens.db import Database
from costlens.db.tenant import set_tenant
from costlens.models import CostAnomaly
from tests.helpers import add_to_org, make_org, sign_in_client

END = date(2026, 11, 14)


def series_lines(spike_day: date | None = None, spike: float = 0, service: str = "Redshift"):
    lines = []
    for i in range(60):
        day = END - timedelta(days=59 - i)
        base = 1000.0 if day.weekday() < 5 else 600.0  # weekly seasonality
        lines.append(
            CostLine(
                charge_date=day,
                provider="aws",
                service_name="EC2",
                sub_account_id="1",
                billed_cost=Decimal(str(base)),
                effective_cost=Decimal(str(base)),
                billing_currency="USD",
            )
        )
        extra = 200.0 + (spike if day == spike_day else 0)
        lines.append(
            CostLine(
                charge_date=day,
                provider="aws",
                service_name=service,
                sub_account_id="2",
                billed_cost=Decimal(str(extra)),
                effective_cost=Decimal(str(extra)),
                billing_currency="USD",
            )
        )
    return lines


def load(db: Database, org_id, lines) -> None:
    months: dict[date, list] = {}
    for line in lines:
        months.setdefault(line.charge_date.replace(day=1), []).append(line)
    with db.session() as s:
        for month, items in months.items():
            replace_billing_period(
                s, org_id=org_id, source_kind="t", source_key="t", billing_period=month, lines=items
            )


def found(db: Database, org_id) -> set[tuple[str, str]]:
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        return {(a.dimension, a.key) for a in s.scalars(select(CostAnomaly))}


def test_baseline_respects_weekdays() -> None:
    history = [
        (
            END - timedelta(days=28 - i),
            1000.0 if (END - timedelta(days=28 - i)).weekday() < 5 else 600.0,
        )
        for i in range(28)
    ]
    saturday = date(2026, 11, 14)
    assert saturday.weekday() == 5
    expected, spread = anomalies.baseline(history, saturday)  # type: ignore[misc]
    assert expected == pytest.approx(600)
    assert spread == pytest.approx(12)  # flat series: the 2% floor
    assert anomalies.baseline(history[:5], saturday) is None


def test_spike_detected_and_normal_days_not(db: Database) -> None:
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    load(db, org_id, series_lines(spike_day=END, spike=900))
    with db.session() as s:
        created = detect(s, org_id)
    keys = {(a.dimension, a.key) for a in created}
    # One incident, reported once at its most specific level: the account and the total
    # went up too, but the Redshift service anomaly explains it.
    assert keys == {("service", "Redshift")}
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        red = s.scalar(select(CostAnomaly).where(CostAnomaly.key == "Redshift"))
        assert red is not None and red.actual == 1100 and red.expected == pytest.approx(200)
        assert red.impact == pytest.approx(900) and red.status == "open"


def test_small_or_proportionally_tiny_changes_ignored(db: Database) -> None:
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    load(db, org_id, series_lines(spike_day=END, spike=40))  # below the 50 minimum impact
    with db.session() as s:
        assert detect(s, org_id) == []
    with db.session() as s:
        assert detect(s, org_id, settings=AlertSettings(min_impact=10, sensitivity="high")) != []


def test_detection_is_idempotent(db: Database) -> None:
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    load(db, org_id, series_lines(spike_day=END, spike=900))
    with db.session() as s:
        first = len(detect(s, org_id))
    with db.session() as s:
        assert detect(s, org_id) == []
    assert len(found(db, org_id)) == first


def test_not_enough_history(db: Database) -> None:
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    load(db, org_id, series_lines(spike_day=END, spike=900)[-20:])
    with db.session() as s:
        assert detect(s, org_id) == []


def test_alert_email_to_owners_and_admins_once(db: Database) -> None:
    org_id, _ = make_org(db, "Acme", "owner@acme.com")
    add_to_org(db, org_id, "admin@acme.com", Role.ADMIN)
    add_to_org(db, org_id, "viewer@acme.com", Role.VIEWER)
    load(db, org_id, series_lines(spike_day=END, spike=900))
    with db.session() as s:
        detect(s, org_id)
        emails = pending_alert(s, org_id, "https://app.costlens.example")
    assert sorted(e.to for e in emails) == ["admin@acme.com", "owner@acme.com"]
    assert "unusual cost increase" in emails[0].subject
    assert "Redshift" in emails[0].text and "https://app.costlens.example/costs" in emails[0].text
    with db.session() as s:
        assert pending_alert(s, org_id, "x") == []  # already notified


def test_alerts_can_be_disabled(db: Database) -> None:
    from costlens.models import Organization

    org_id, _ = make_org(db, "Acme", "owner@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        org = s.get(Organization, org_id)
        assert org is not None
        org.settings = {"alerts": {"anomaly_alerts": False}}
    load(db, org_id, series_lines(spike_day=END, spike=900))
    with db.session() as s:
        detect(s, org_id)
        assert pending_alert(s, org_id, "x") == []


def test_api_list_update_and_settings(client: TestClient, db: Database) -> None:
    org_id, owner = make_org(db, "Acme", "owner@acme.com")
    viewer = add_to_org(db, org_id, "viewer@acme.com", Role.VIEWER)
    load(db, org_id, series_lines(spike_day=END, spike=900))
    with db.session() as s:
        detect(s, org_id)
    c = sign_in_client(client, db, viewer, org_id)
    items = c.get("/api/v1/costs/anomalies").json()
    assert items and items[0]["impact"] > 0
    aid = items[0]["id"]
    assert c.patch(f"/api/v1/costs/anomalies/{aid}", json={"status": "expected"}).status_code == 403
    assert c.put("/api/v1/org/alert-settings", json={"sensitivity": "high"}).status_code == 403
    c = sign_in_client(client, db, owner, org_id)
    r = c.patch(
        f"/api/v1/costs/anomalies/{aid}", json={"status": "expected", "note": "Planned load test"}
    )
    assert r.json()["status"] == "expected" and r.json()["note"] == "Planned load test"
    assert all(a["id"] != aid for a in c.get("/api/v1/costs/anomalies").json())
    assert any(
        a["id"] == aid for a in c.get("/api/v1/costs/anomalies", params={"status": "all"}).json()
    )
    saved = c.put(
        "/api/v1/org/alert-settings", json={"sensitivity": "high", "min_impact": 25}
    ).json()
    assert saved == {
        "anomaly_alerts": True,
        "sensitivity": "high",
        "min_impact": 25.0,
        "ai_explanations": False,
        "forecast_warnings": True,
        "forecast_over_pct": 10.0,
        "same_day_warnings": True,
        "same_day_over_pct": 30.0,
        "weekly_digest_email": False,
    }
    assert c.get("/api/v1/org/alert-settings").json() == saved
    trail = c.get("/api/v1/org/audit-events", params={"action": "cost_anomaly"}).json()["items"]
    assert trail[0]["changes"]["status"] == ["open", "expected"]


def test_file_import_triggers_detection_and_alert(
    client: TestClient, db: Database, settings
) -> None:
    from costlens.core.email import configure_email
    from costlens.jobs import load_all_jobs

    load_all_jobs()
    outbox = MemorySender()
    configure_email(outbox)
    org_id, owner = make_org(db, "Acme", "owner@acme.com")
    rows = ["Date,Service,Cost"]
    for line in series_lines(spike_day=END, spike=900):
        rows.append(f"{line.charge_date},{line.service_name},{line.billed_cost}")
    c = sign_in_client(client, db, owner, org_id)
    up = c.post(
        "/api/v1/imports/files",
        params={"filename": "x.csv"},
        content=("\n".join(rows) + "\n").encode(),
    ).json()
    c.post(
        f"/api/v1/imports/files/{up['id']}/import",
        json={
            "mapping": {"charge_date": "Date", "service_name": "Service", "cost": "Cost"},
            "currency": "USD",
        },
    )
    assert ("service", "Redshift") in found(db, org_id)
    assert [m.to for m in outbox.outbox] == ["owner@acme.com"]


def test_demo_flags_the_redshift_spike(db: Database) -> None:
    from costlens.demo.seed import ensure_demo, load_all_seeders, seeders

    load_all_seeders()
    if "costs.anomalies" not in seeders():
        pytest.fail("demo seeders not registered")
    with db.session() as s:
        org_id = ensure_demo(s).id
    keys = found(db, org_id)
    assert ("service", "Amazon Redshift") in keys
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        assert len(s.scalars(select(CostAnomaly)).all()) <= 3  # one incident, not 18 rows


def test_increase_spread_over_services_reported_for_the_account(db: Database) -> None:
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    lines = series_lines()
    for svc in ("Glue", "Athena", "EMR", "Kinesis"):  # each +40/day: below the 50 minimum
        for i in range(60):
            day = END - timedelta(days=59 - i)
            amount = 100.0 + (60.0 if day == END else 0.0)
            lines.append(
                CostLine(
                    charge_date=day,
                    provider="aws",
                    service_name=svc,
                    sub_account_id="9",
                    billed_cost=Decimal(str(amount)),
                    effective_cost=Decimal(str(amount)),
                    billing_currency="USD",
                )
            )
    load(db, org_id, lines)
    with db.session() as s:
        keys = {
            (a.dimension, a.key) for a in detect(s, org_id, settings=AlertSettings(min_impact=100))
        }
    assert ("account", "9") in keys  # +240 in account 9, no single service explains it


def test_consecutive_days_form_one_episode(db: Database) -> None:
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    lines = series_lines()
    for line in lines:
        if line.service_name == "Redshift" and line.charge_date >= END - timedelta(days=2):
            line.billed_cost = line.effective_cost = Decimal(1100)
    load(db, org_id, lines)
    for back in (2, 1, 0):
        with db.session() as s:
            detect(s, org_id, through=END - timedelta(days=back), recent_days=1)
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        episodes = s.scalars(select(CostAnomaly).where(CostAnomaly.key == "Redshift")).all()
    assert len(episodes) == 1
    ep = episodes[0]
    assert (ep.day, ep.last_day, ep.days) == (END - timedelta(days=2), END, 3)
    assert ep.impact > ep.peak_impact > 0
