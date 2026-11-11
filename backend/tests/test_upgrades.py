from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from ccradar.costs.ingest import CostLine, replace_billing_period
from ccradar.db import Database
from ccradar.db.base import utcnow
from ccradar.db.tenant import set_tenant
from ccradar.findings.service import run
from ccradar.findings.upgrades import aurora_support, coming_up
from ccradar.inventory.aws import Found
from ccradar.inventory.service import upsert
from tests.helpers import make_org, sign_in_client

RDS = "Amazon Relational Database Service"


def test_aurora_calendar() -> None:
    assert aurora_support("aurora-mysql", "5.7.mysql_aurora.2.11.2") == (
        date(2024, 10, 31),
        "Aurora MySQL 3 (MySQL 8.0 compatible)",
    )
    assert aurora_support("aurora-postgresql", "12.9") == (
        date(2025, 2, 28),
        "Aurora PostgreSQL 16",
    )
    assert aurora_support("aurora-postgresql", "16.1") is None
    assert aurora_support("aurora-mysql", "8.0.mysql_aurora.3.05.2") is None


def test_upgrade_findings_and_overview(client: TestClient, db: Database, monkeypatch) -> None:
    from ccradar.findings import aws_compute

    monkeypatch.setattr(aws_compute, "_usd", lambda db, org_id, currency: 1.0)
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        upsert(
            s,
            org_id,
            "1",
            [
                Found(
                    RDS,
                    "rds_cluster",
                    "arn:cluster:old",
                    "r",
                    name="legacy",
                    state="available",
                    attributes={
                        "engine": "aurora-mysql",
                        "engine_version": "5.7.mysql_aurora.2.11.2",
                    },
                ),
                Found(
                    RDS,
                    "rds_instance",
                    "arn:db:legacy-1",
                    "r",
                    name="legacy-1",
                    state="available",
                    attributes={
                        "cluster": "legacy",
                        "instance_class": "db.r6g.xlarge",
                        "engine": "aurora-mysql",
                    },
                ),
                Found(
                    RDS,
                    "rds_cluster",
                    "arn:cluster:billed",
                    "r",
                    name="billed",
                    state="available",
                    attributes={"engine": "aurora-postgresql", "engine_version": "11.21"},
                ),
                Found(
                    "ElastiCache",
                    "elasticache_cluster",
                    "arn:cache:r5",
                    "r",
                    name="sessions",
                    state="available",
                    attributes={"engine": "redis", "engine_version": "5.0.6"},
                ),
                Found(
                    "OpenSearch",
                    "opensearch_domain",
                    "arn:es:logs",
                    "r",
                    name="logs",
                    state="active",
                    attributes={"engine_version": "Elasticsearch_5.6"},
                ),
                Found(
                    "EKS",
                    "eks_cluster",
                    "arn:eks:soon",
                    "r",
                    name="soon",
                    state="active",
                    attributes={"version": "1.32"},
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
            usage_type=usage,
            billed_cost=Decimal(c),
            effective_cost=Decimal(c),
            billing_currency="USD",
        )
        for d in range(1, 31)
        for rid, usage, c in (
            ("arn:cluster:billed", "USE1-ExtendedSupport:Yr1-Yr2:AuroraPostgreSQL11", "5"),
            ("arn:cache:r5", "NodeUsage:cache.r6g.large", "9"),
            ("arn:es:logs", "USE1-ExtendedSupport:ES", "2"),
        )
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
        run(
            s,
            org_id,
            only=[
                "aws.aurora_extended_support",
                "aws.elasticache_extended_support",
                "aws.opensearch_extended_support",
            ],
        )
        soon = coming_up(s, today=date(2027, 1, 1))
    c = sign_in_client(client, db, owner, org_id)
    data = c.get("/api/v1/findings/upgrades").json()
    items = {i["title"]: i for i in data["items"]}
    legacy = items["Upgrade Aurora cluster legacy out of Extended Support"]
    year3 = today > date(2026, 10, 31)
    rate = 0.2 if year3 else 0.1
    assert legacy["basis"] == "list price" and legacy["staying_monthly"] == pytest.approx(
        4 * rate * 24 * 30.4, abs=0.01
    )
    assert items["Upgrade Aurora cluster billed out of Extended Support"][
        "staying_monthly"
    ] == pytest.approx(5 * 30.4, abs=0.01)
    assert items["Upgrade OpenSearch domain logs out of Extended Support"]["basis"] == "billing"
    cache = [i for i in data["items"] if i["title"].startswith("Upgrade cache sessions")]
    if today > date(2026, 1, 31):
        assert cache and cache[0]["staying_monthly"] == pytest.approx(
            9 * 30.4 * 0.8 / 1.8, abs=0.01
        )
    assert [r["resource"] for r in soon] == ["soon"] and soon[0][
        "kind"
    ] == "EKS"  # 1.32 ends 23 Mar 2027
