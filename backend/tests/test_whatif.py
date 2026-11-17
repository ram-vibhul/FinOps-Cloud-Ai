"""What-if planner (11.39)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from ccradar.costs.ingest import CostLine, replace_billing_period
from ccradar.db import Database
from ccradar.db.tenant import set_tenant
from ccradar.models import Finding
from tests.helpers import make_org, sign_in_client

NOW = datetime(2026, 11, 10, tzinfo=UTC)


def finding(org_id, detector: str, category: str, resource: str, saving: float) -> Finding:
    return Finding(org_id=org_id, detector=detector, category=category,
                   dedup_key=f"{detector}:{uuid.uuid4().hex}", overlap_key=resource,
                   title=f"{detector} {resource}", savings_monthly=saving, currency="USD",
                   risk="low", effort="low", first_seen_at=NOW, last_seen_at=NOW,
                   provider="aws", resource_id=resource)  # fmt: skip


def test_levers_count_each_resource_once(client: TestClient, db: Database) -> None:
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    lines = [
        CostLine(
            charge_date=date(2026, 11, 1) + timedelta(days=i),
            provider="aws",
            service_name="EC2",
            billed_cost=Decimal(100),
            effective_cost=Decimal(100),
            billing_currency="USD",
        )
        for i in range(10)
    ]
    with db.session() as s:
        replace_billing_period(s, org_id=org_id, source_kind="t", source_key="t",
                               billing_period=date(2026, 11, 1), lines=lines)  # fmt: skip
        set_tenant(s, org_id=org_id)
        s.add(finding(org_id, "aws.graviton", "rightsizing", "i-1", 50))
        s.add(finding(org_id, "aws.nonprod_schedule", "schedule", "i-1", 120))  # same server
        s.add(finding(org_id, "aws.ebs_unattached", "waste", "vol-1", 30))
        s.add(finding(org_id, "aws.ec2_rightsizing", "rightsizing", "i-2", 40))
        closed = finding(org_id, "aws.eip_idle", "waste", "eip-1", 999)
        closed.status = "resolved"
        s.add(closed)
    c = sign_in_client(client, db, owner, org_id)
    none = c.post("/api/v1/what-if", json={"levers": []}).json()
    assert none["base_monthly"] == 1000.0 and none["total_monthly"] == 0
    levers = {x["id"]: x["monthly"] for x in none["levers"]}
    assert levers == {"graviton": 50, "schedule": 120, "rightsizing": 40, "waste": 30, "other": 0}
    both = c.post("/api/v1/what-if", json={"levers": ["graviton", "schedule", "waste"]}).json()
    assert both["levers_monthly"] == 150.0  # i-1 once (largest, 120) + vol-1 30
    assert both["after_monthly"] == 850.0 and both["saving_pct"] == 15.0
    sp = c.post("/api/v1/what-if", json={"levers": [], "savings_plan": {"term": "1yr"}}).json()
    assert sp["commitment"]["monthly"] == 0.0 and sp["commitment"]["reason"]  # no compute data
    assert c.post("/api/v1/what-if", json={"levers": ["teleport"]}).status_code == 422
