"""Radar reconciliation (11.27): estimates compared with the bill."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from ccradar.changes.reconcile import accuracy, reconcile
from ccradar.costs.ingest import CostLine, replace_billing_period
from ccradar.db import Database
from ccradar.db.tenant import set_tenant
from ccradar.models import ChangeEvent
from tests.helpers import make_org, sign_in_client

LAUNCH = datetime(2026, 11, 1, 15, 0, tzinfo=UTC)


def change(org_id, ext: str, rid: str | None, monthly: float, label: str) -> ChangeEvent:
    impact = {"monthly": monthly, "currency": "USD", "source": "list price", "label": label}
    return ChangeEvent(
        org_id=org_id,
        source="cloudtrail",
        kind="infrastructure",
        title="RunInstances",
        external_id=ext,
        occurred_at=LAUNCH,
        resource_id=rid,
        attributes={"event_name": "RunInstances", "impact": impact},
    )


def line(day: date, resource: str, cost: int) -> CostLine:
    return CostLine(
        charge_date=day,
        provider="aws",
        service_name="EC2",
        resource_id=resource,
        billed_cost=Decimal(cost),
        effective_cost=Decimal(cost),
        billing_currency="USD",
    )


def test_measures_launches_once_billed_and_publishes_accuracy(
    client: TestClient, db: Database
) -> None:
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    lines = []
    for i in range(10):  # 1 to 10 November
        day = date(2026, 11, 1) + timedelta(days=i)
        lines.append(line(day, "i-0aaa", 10))
        lines.append(line(day, "arn:aws:ec2:ap-south-1:1:volume/vol-0bbb", 1))
    with db.session() as s:
        replace_billing_period(s, org_id=org_id, source_kind="t", source_key="t",
                               billing_period=date(2026, 11, 1), lines=lines)  # fmt: skip
        set_tenant(s, org_id=org_id)
        s.add(change(org_id, "e1", "i-0aaa", 1216.0, "4 x m5.xlarge"))  # 304 per server
        s.add(change(org_id, "e2", "vol-0bbb", 40.0, "volume 500 GB gp3"))  # ARN suffix match
        s.add(change(org_id, "e3", "i-0zzz", 100.0, "1 x t3.large"))  # not in the bill
        s.add(change(org_id, "e4", None, 50.0, "NAT gateway"))  # no resource: not measured
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        assert reconcile(s, org_id) == 2
        assert reconcile(s, org_id) == 0  # seven billed days already: done
        acc = accuracy(s)
    # server: 10 a day = 304 a month, as estimated; volume: 30.4 against 40, 24% under
    assert acc == {
        "measured": 2,
        "median_error_pct": 12.0,
        "within_pct": 25.0,
        "share_within": 100.0,
    }
    feed = sign_in_client(client, db, owner, org_id).get("/api/v1/radar", params={"hours": 720})
    server = next(i for i in feed.json()["items"] if (i["measured"] or {}).get("estimate") == 304.0)
    assert server["measured"] == {"monthly": 304.0, "estimate": 304.0, "error_pct": 0.0,
                                  "days": 7, "through": "2026-11-08"}  # fmt: skip
    assert feed.json()["accuracy"]["measured"] == 2
