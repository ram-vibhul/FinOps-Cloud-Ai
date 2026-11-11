from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from ccradar.costs import early_warnings
from ccradar.costs.ingest import CostLine, replace_billing_period
from ccradar.db import Database
from ccradar.db.base import utcnow
from ccradar.db.tenant import set_tenant
from ccradar.models import CostHourly
from tests.helpers import make_org, sign_in_client


def _hours(s, org_id, day: datetime, per_hour: dict[str, float], hours: int) -> None:
    for h in range(hours):
        for service, cost in per_hour.items():
            s.add(
                CostHourly(
                    org_id=org_id,
                    hour=day + timedelta(hours=h),
                    source_key="cur",
                    service_name=service,
                    billing_currency="USD",
                    effective_cost=Decimal(str(cost)),
                )
            )


def test_same_day_and_forecast(client: TestClient, db: Database, monkeypatch) -> None:
    sent: list = []
    monkeypatch.setattr(
        early_warnings, "notify", lambda db, event, messages: sent.extend(m.title for m in messages)
    )
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    now = datetime.now(UTC).replace(minute=30, second=0, microsecond=0)
    if now.hour < 6:
        now = now.replace(hour=10)
    today = now.replace(hour=0, minute=0)
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        for d in range(1, 8):  # the usual: 10/hour
            _hours(s, org_id, today - timedelta(days=d), {"Amazon EC2": 8, "Amazon S3": 2}, 24)
        _hours(
            s, org_id, today, {"Amazon EC2": 20, "Amazon S3": 2}, now.hour
        )  # today: EC2 running hot
    # Last month 100/day; this month 200/day so far: the forecast is far above.
    first = utcnow().date().replace(day=1)
    prev = (first - timedelta(days=1)).replace(day=1)
    lines = [
        CostLine(
            charge_date=prev + timedelta(days=i),
            provider="aws",
            service_name="Amazon EC2",
            billed_cost=Decimal(100),
            effective_cost=Decimal(100),
            billing_currency="USD",
        )
        for i in range((first - prev).days)
    ]
    lines += [
        CostLine(
            charge_date=first + timedelta(days=i),
            provider="aws",
            service_name="Amazon EC2",
            billed_cost=Decimal(200),
            effective_cost=Decimal(200),
            billing_currency="USD",
        )
        for i in range(max(utcnow().date().day - 1, 1))
    ]
    with db.session() as s:
        for month in sorted({ln.charge_date.replace(day=1) for ln in lines}):
            replace_billing_period(
                s,
                org_id=org_id,
                source_kind="t",
                source_key=str(month),
                billing_period=month,
                lines=[ln for ln in lines if ln.charge_date.replace(day=1) == month],
            )
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        warnings = {w["kind"]: w for w in early_warnings.current(s, org_id, now)}
        assert (
            warnings["same_day"]["usual"] == 10 * now.hour
            and warnings["same_day"]["so_far"] == 22 * now.hour
        )
        assert warnings["same_day"]["drivers"][0]["key"] == "Amazon EC2"
        assert warnings["forecast"]["over_pct"] > 10
        assert early_warnings.check(s, org_id, now) == 2
        assert early_warnings.check(s, org_id, now) == 0  # once a day / once a month
    assert any(t.startswith("Spend today is 120% above normal") for t in sent)
    c = sign_in_client(client, db, owner, org_id)
    assert {w["kind"] for w in c.get("/api/v1/costs/early-warnings").json()} >= {"forecast"}
    assert (
        c.put(
            "/api/v1/org/alert-settings",
            json={"same_day_warnings": False, "forecast_warnings": False},
        ).status_code
        == 200
    )
    assert c.get("/api/v1/costs/early-warnings").json() == []
