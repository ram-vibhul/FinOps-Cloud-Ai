from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from costlens.aws_audit.service import run_audit
from costlens.costs.ingest import CostLine, replace_billing_period
from costlens.db import Database
from costlens.db.base import utcnow
from costlens.db.tenant import set_tenant
from costlens.findings.service import run
from costlens.inventory.aws import Found
from costlens.inventory.service import upsert
from costlens.models import AuditResult, Finding, ReliabilityTarget
from tests.helpers import make_org

EC2, RDS = "Amazon Elastic Compute Cloud", "Amazon Relational Database Service"


def _seed(db: Database):
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    dev = {"env": "dev"}
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        upsert(
            s,
            org_id,
            "1",
            [
                Found(
                    EC2,
                    "ec2_instance",
                    "i-dev",
                    "r",
                    name="api-dev",
                    state="running",
                    tags=dev,
                    attributes={"instance_type": "m6i.large", "platform": "Linux/UNIX"},
                ),
                Found(
                    EC2,
                    "ec2_instance",
                    "i-sched",
                    "r",
                    name="ci",
                    state="running",
                    tags={**dev, "Schedule": "office"},
                    attributes={"instance_type": "t3.large"},
                ),
                Found(
                    EC2,
                    "ec2_instance",
                    "i-prod",
                    "r",
                    name="web",
                    state="running",
                    tags={"env": "production"},
                    attributes={"instance_type": "c5.xlarge", "platform": "Linux/UNIX"},
                ),
                Found(
                    EC2,
                    "ec2_instance",
                    "i-win",
                    "r",
                    name="ad",
                    state="running",
                    attributes={"instance_type": "m5.large", "platform": "Windows"},
                ),
                Found(
                    EC2,
                    "ec2_instance",
                    "i-arm",
                    "r",
                    name="arm",
                    state="running",
                    attributes={"instance_type": "m7g.large"},
                ),
                Found(
                    RDS,
                    "rds_instance",
                    "arn:db:qa",
                    "r",
                    name="orders-qa",
                    state="available",
                    attributes={"instance_class": "db.r5.large", "engine": "postgres"},
                ),
                Found(
                    RDS,
                    "rds_instance",
                    "arn:db:ora",
                    "r",
                    name="ledger",
                    state="available",
                    attributes={"instance_class": "db.r5.large", "engine": "oracle-ee"},
                ),
            ],
        )
    today = utcnow().date()
    lines = [
        CostLine(
            charge_date=today - timedelta(days=d),
            provider="aws",
            service_name="svc",
            resource_id=rid,
            billed_cost=Decimal(10),
            effective_cost=Decimal(10),
            billing_currency="USD",
        )
        for d in range(1, 31)
        for rid in ("i-dev", "i-sched", "i-prod", "i-win", "i-arm", "arn:db:qa", "arn:db:ora")
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
    return org_id


def test_schedules_and_graviton(db: Database) -> None:
    org_id = _seed(db)
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        run(s, org_id, only=["aws.nonprod_schedule", "aws.graviton"])
        rows = {f.dedup_key: f for f in s.scalars(select(Finding))}
        audit = run_audit(s, org_id, run_detectors=False)
        checks = {
            r.check_id: r
            for r in s.scalars(select(AuditResult).where(AuditResult.run_id == audit.id))
        }
    month = 10 * 30.4
    sched = {k for k in rows if k.startswith("aws.nonprod_schedule")}
    assert sched == {
        "aws.nonprod_schedule:i-dev",
        "aws.nonprod_schedule:arn:db:qa",
    }  # tagged one skipped
    # Graviton comes first in the pipeline; the schedule is sized on what is left.
    assert float(rows["aws.nonprod_schedule:i-dev"].savings_monthly) == pytest.approx(
        month * (1 - 0.18) * (1 - 60 / 168), abs=0.01
    )
    assert "stop-db-instance" in rows["aws.nonprod_schedule:arn:db:qa"].commands[0]
    grav = {k for k in rows if k.startswith("aws.graviton")}
    # Windows, already-Arm and Oracle are skipped; the scheduled t3 still fits Graviton (t4g).
    assert grav == {
        f"aws.graviton:graviton:{r}" for r in ("i-dev", "i-sched", "i-prod", "arn:db:qa")
    }
    prod = rows["aws.graviton:graviton:i-prod"]
    assert "c7g.xlarge" in prod.title and float(prod.savings_monthly) == pytest.approx(
        month * 0.18, abs=0.01
    )
    assert float(rows["aws.graviton:graviton:arn:db:qa"].savings_monthly) == pytest.approx(
        month * 0.1, abs=0.01
    )
    assert checks["nonprod.schedule"].status == "saving" and checks["graviton"].status == "saving"


def test_critical_target_blocks_the_schedule(db: Database) -> None:
    org_id = _seed(db)
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        s.add(
            ReliabilityTarget(
                org_id=org_id,
                name="api",
                tier="critical",
                match={"kind": "aws_resource", "resource_id": "i-dev"},
            )
        )
        s.flush()
        run(s, org_id, only=["aws.nonprod_schedule"])
        keys = {f.dedup_key for f in s.scalars(select(Finding))}
    assert (
        "aws.nonprod_schedule:i-dev" not in keys
    )  # stop commands are never suggested for critical
    assert "aws.nonprod_schedule:arn:db:qa" in keys
