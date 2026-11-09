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
from costlens.findings.aws_db_perf import gp3_iops_cost, smaller_node
from costlens.findings.service import run
from costlens.inventory.aws import Found
from costlens.inventory.service import upsert
from costlens.models import AuditResult, Finding
from tests.helpers import make_org
from tests.test_rightsizing_aws import add_metrics

RDS = "Amazon Relational Database Service"


def test_helpers() -> None:
    assert smaller_node("cache.r6g.xlarge") == "cache.r6g.large"
    assert (
        smaller_node("cache.r6g.large") is None
        and smaller_node("cache.t4g.small") == "cache.t4g.micro"
    )
    assert gp3_iops_cost(100, 2500) == 0 and gp3_iops_cost(100, 5000) == pytest.approx(40)
    assert gp3_iops_cost(500, 12000) == 0  # 400 GB and up: 12,000 IOPS included


def test_detectors_and_notes(db: Database, monkeypatch) -> None:
    from costlens.findings import aws_compute

    monkeypatch.setattr(aws_compute, "_usd", lambda db, org_id, currency: 1.0)
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        upsert(
            s,
            org_id,
            "1",
            [
                Found(
                    RDS,
                    "rds_instance",
                    "arn:db:io1",
                    "r",
                    name="ledger",
                    state="available",
                    attributes={
                        "storage_type": "io1",
                        "storage_gb": 200,
                        "iops": 10000,
                        "instance_class": "db.r6g.large",
                        "engine": "postgres",
                        "engine_version": "16.2",
                    },
                ),
                Found(
                    RDS,
                    "rds_instance",
                    "arn:db:busy",
                    "r",
                    name="orders",
                    state="available",
                    attributes={
                        "storage_type": "gp2",
                        "storage_gb": 100,
                        "instance_class": "db.m6g.large",
                        "engine": "mysql",
                        "engine_version": "5.7.44",
                    },
                ),
                Found(
                    "ElastiCache",
                    "elasticache_cluster",
                    "arn:cache:big",
                    "r",
                    name="sessions",
                    state="available",
                    attributes={"node_type": "cache.r6g.xlarge"},
                ),
                Found(
                    "ElastiCache",
                    "elasticache_cluster",
                    "arn:cache:full",
                    "r",
                    name="catalog",
                    state="available",
                    attributes={"node_type": "cache.r6g.large"},
                ),
            ],
        )
        add_metrics(s, org_id, "arn:db:io1", "read_iops", 1500)
        add_metrics(s, org_id, "arn:db:io1", "write_iops", 500)
        add_metrics(s, org_id, "arn:db:busy", "connections", 700)  # 8 GiB MySQL: ~683 allowed
        add_metrics(s, org_id, "arn:db:busy", "read_iops", 900)  # gp2 100 GB: 300 baseline
        add_metrics(s, org_id, "arn:db:busy", "disk_queue", 25)
        add_metrics(s, org_id, "arn:cache:big", "cache_memory_pct", 20)
        add_metrics(s, org_id, "arn:cache:big", "engine_cpu", 15)
        add_metrics(s, org_id, "arn:cache:big", "evictions", 0)
        add_metrics(s, org_id, "arn:cache:full", "cache_memory_pct", 97)
        add_metrics(s, org_id, "arn:cache:full", "evictions", 50)
        add_metrics(s, org_id, "arn:cache:full", "cache_hit_rate", 120)  # avg = max / 2 = 60%
    today = utcnow().date()
    lines = [
        CostLine(
            charge_date=today - timedelta(days=d),
            provider="aws",
            service_name="ElastiCache",
            resource_id="arn:cache:big",
            billed_cost=Decimal(10),
            effective_cost=Decimal(10),
            billing_currency="USD",
        )
        for d in range(1, 31)
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
        run(s, org_id, only=["aws.rds_io1_to_gp3", "aws.elasticache_rightsizing"])
        rows = {f.dedup_key: f for f in s.scalars(select(Finding))}
        audit = run_audit(s, org_id, run_detectors=False)
        checks = {
            r.check_id: r
            for r in s.scalars(select(AuditResult).where(AuditResult.run_id == audit.id))
        }
    io1 = rows["aws.rds_io1_to_gp3:arn:db:io1"]
    # io1: 200*0.125 + 10000*0.10 = 1025. gp3 for 2,600 IOPS (peak + 30%) is inside the
    # 3,000 baseline: 200*0.115 = 23.
    assert float(io1.savings_monthly) == pytest.approx(1025 - 23, abs=0.01)
    assert "--iops 3000" in io1.commands[0]
    cache = rows["aws.elasticache_rightsizing:arn:cache:big"]
    assert "cache.r6g.large" in cache.title and float(cache.savings_monthly) == pytest.approx(
        10 * 30.4 / 2, abs=0.01
    )
    assert "aws.elasticache_rightsizing:arn:cache:full" not in rows
    perf = checks["rds.performance"].reason
    for text in (
        "connection limit",
        "queue storage requests",
        "burst above their baseline",
        "older major versions",
    ):
        assert text in perf, text
    assert "ledger" not in perf.split("older major versions")[-1]  # PostgreSQL 16 is current
    cache_notes = checks["elasticache.sizing"]
    assert cache_notes.status == "saving"  # the finding wins over the notes
    assert checks["rds.storage"].status == "saving"
    from costlens.aws_audit.inspections import cache_performance
    from costlens.aws_audit.service import Facts

    with db.session() as s:
        set_tenant(s, org_id=org_id)
        notes = cache_performance(Facts(s, org_id, "USD"))
    assert any("full and evicting" in n and "catalog" in n for n in notes)
    assert any("under 80% of reads" in n for n in notes)
