from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from ccradar.costs.commitment_plan import percentile, reservation_plan, savings_plan_plan
from ccradar.costs.ingest import CostLine, replace_billing_period
from ccradar.db import Database
from ccradar.db.base import utcnow
from ccradar.db.tenant import set_tenant
from ccradar.findings.registry import FindingIn
from ccradar.findings.service import _store
from ccradar.models import Commitment
from tests.helpers import make_org, sign_in_client


def test_percentile() -> None:
    assert percentile([1, 2, 3, 4, 5], 50) == 3 and percentile([10, 20], 10) == 11
    assert percentile([], 10) == 0


def _bill(db: Database, org_id, today: date) -> None:
    lines = []
    for d in range(1, 41):
        day = today - timedelta(days=d)
        daily = 240.0 + d  # 10/hour plus a little; the newest day is skipped as incomplete
        lines.append(
            CostLine(
                charge_date=day,
                provider="aws",
                service_name="Amazon Elastic Compute Cloud",
                usage_type="BoxUsage:m6i.large",
                billed_cost=Decimal(str(daily)),
                effective_cost=Decimal(str(daily)),
                billing_currency="USD",
            )
        )
        lines.append(
            CostLine(
                charge_date=day,
                provider="aws",
                service_name="Amazon Elastic Compute Cloud",
                usage_type="BoxUsage:m6i.large",
                pricing_category="Committed",
                billed_cost=Decimal(500),
                effective_cost=Decimal(300),
                billing_currency="USD",
            )
        )
        lines.append(
            CostLine(
                charge_date=day,
                provider="aws",
                service_name="Amazon Elastic Compute Cloud",
                usage_type="EBS:VolumeUsage.gp3",
                billed_cost=Decimal(80),
                effective_cost=Decimal(80),
                billing_currency="USD",
            )
        )
        for rid, hours in (
            ("arn:db:a", 24),
            ("arn:db:b", 24),
            ("arn:db:c", 24 if d % 2 else 0),
            ("arn:db:x", 24),
        ):
            if hours:
                lines.append(
                    CostLine(
                        charge_date=day,
                        provider="aws",
                        service_name="Amazon Relational Database Service",
                        usage_type="APS3-InstanceUsage:db.r6g.large",
                        resource_id=rid,
                        region="ap-south-1",
                        consumed_quantity=Decimal(hours),
                        billed_cost=Decimal("6"),
                        effective_cost=Decimal("6"),
                        billing_currency="USD",
                    )
                )
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


def test_plans(db: Database) -> None:
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    today = utcnow().date()
    _bill(db, org_id, today)
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        # Pending right-sizing removes 73 USD a month of compute (0.10/hour).
        _store(
            s,
            org_id,
            "aws.ec2_rightsizing",
            "rightsizing",
            [
                FindingIn(
                    key="size:i-1",
                    title="Downsize",
                    savings_monthly=73,
                    currency="USD",
                    provider="aws",
                    resource_id="i-1",
                    resource_type="ec2_instance",
                )
            ],
        )
        _store(
            s,
            org_id,
            "aws.rds_rightsizing",
            "database",
            [
                FindingIn(
                    key="idle:arn:db:x",
                    title="Delete x",
                    savings_monthly=100,
                    currency="USD",
                    provider="aws",
                    resource_id="arn:db:x",
                    resource_type="rds_instance",
                )
            ],
        )
        s.add(
            Commitment(
                org_id=org_id,
                kind="savings_plan",
                commitment_id="sp",
                description="Compute",
                state="active",
                hourly_commitment=2.0,
                ends_at=datetime.combine(today + timedelta(days=100), datetime.min.time(), UTC),
                alerts_sent=[],
                seen_at=utcnow(),
            )
        )
        s.flush()
        sp = savings_plan_plan(
            s,
            org_id,
            "USD",
            pct=10,
            term="1yr",
            payment="no_upfront",
            rungs=4,
            interval_months=3,
            days=30,
            today=today,
        )
        ri = reservation_plan(s, org_id, "USD", pct=10, term="1yr", days=30)
    # Daily spend over the 30 complete days runs 242..271: the 10th percentile is 244.9.
    steady = percentile([240.0 + d for d in range(2, 32)], 10) / 24
    assert sp["steady_on_demand_hourly"] == pytest.approx(steady, abs=0.01)
    assert sp["after_changes_on_demand_hourly"] == pytest.approx(steady - 0.1, abs=0.01)
    total = (steady - 0.1) * (1 - 0.27)
    assert sp["total_hourly_commitment"] == pytest.approx(total, abs=0.01)
    rungs = [
        r
        for r in sp["ladder"]
        if r["reason"] != "first rung" and not r["reason"].startswith("replace")
    ]
    assert len(rungs) == 3 and all(
        r["hourly_commitment"] == pytest.approx(total / 4, abs=0.01) for r in sp["ladder"][:1]
    )
    assert any(
        r["reason"].startswith("replace Compute") and r["hourly_commitment"] == 2.0
        for r in sp["ladder"]
    )
    (rds,) = ri
    # a and b always run, c runs every other day (10th percentile: not steady), x is to be deleted.
    assert rds["count"] == 2 and rds["running_peak"] == 3 and rds["instance_type"] == "db.r6g.large"
    assert rds["on_demand_hourly"] == pytest.approx(0.25) and rds["ladder"] == [
        {"when": "now", "count": 1},
        {"when": "in 3 months", "count": 1},
    ]
    assert rds["monthly_saving"] == pytest.approx(2 * 0.25 * 730 * 0.31, abs=0.01)


def test_no_data_and_api(client: TestClient, db: Database) -> None:
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    c = sign_in_client(client, db, owner, org_id)
    r = c.get("/api/v1/commitments/plan").json()
    assert r["savings_plan"]["status"] == "no_data" and r["reservations"] == []
    _bill(db, org_id, utcnow().date())
    r = c.get("/api/v1/commitments/plan", params={"term": "3yr", "rungs": 2}).json()
    assert r["savings_plan"]["status"] == "ok" and len(r["savings_plan"]["ladder"]) == 2
    assert r["savings_plan"]["discount"] == 0.46 and r["reservations"][0][
        "discount"
    ] == pytest.approx(0.496)
    assert c.get("/api/v1/commitments/plan", params={"rungs": 50}).status_code == 422
