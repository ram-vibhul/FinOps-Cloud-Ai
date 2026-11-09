from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from costlens.accounts.roles import Role
from costlens.costs.ingest import CostLine, replace_billing_period
from costlens.db import Database
from costlens.db.tenant import set_tenant
from costlens.findings import aws_db
from costlens.findings.aws_db import support_for
from costlens.findings.service import run
from costlens.inventory.aws import Found
from costlens.inventory.service import upsert
from costlens.models import Finding
from tests.helpers import add_to_org, make_org, sign_in_client

NOW = datetime(2026, 11, 8, 12, tzinfo=UTC)


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(aws_db, "utcnow", lambda: NOW)


@pytest.mark.parametrize(
    ("engine", "version", "major"),
    [
        ("mysql", "5.7.44", "5.7"),
        ("mysql", "8.0.35", "8.0"),
        ("postgres", "11.22", "11"),
        ("postgres", "13.15", "13"),
        ("postgres", "16.3", None),
        ("mysql", "8.4.2", None),
        ("aurora-postgresql", "11.9", None),  # Aurora has its own schedule
    ],
)
def test_versions(engine: str, version: str, major: str | None) -> None:
    s = support_for(engine, version)
    assert (s.major if s else None) == major


def rds(
    name: str, engine: str, version: str, klass: str, multi_az: bool, tags: dict | None = None
) -> Found:
    return Found(
        "Amazon Relational Database Service",
        "rds_instance",
        f"arn:aws:rds:us-east-1:1:db:{name}",
        "us-east-1",
        name=name,
        state="available",
        tags=tags or {},
        attributes={
            "engine": engine,
            "engine_version": version,
            "instance_class": klass,
            "multi_az": multi_az,
        },
    )


def test_database_detectors(db: Database) -> None:
    org_id, _ = make_org(db, "Acme", "owner@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        upsert(
            s,
            org_id,
            "1",
            [
                rds(
                    "legacy-pg", "postgres", "11.22", "db.r6g.2xlarge", True, {"env": "production"}
                ),
                rds("pg13", "postgres", "13.15", "db.m6g.large", False),
                rds("billed-mysql", "mysql", "5.7.44", "db.m5.xlarge", False),
                rds("current", "postgres", "16.3", "db.m6g.large", False),
                rds("aurora", "aurora-postgresql", "11.9", "db.r6g.large", False),
                rds(
                    "orders-staging",
                    "postgres",
                    "16.3",
                    "db.m6g.large",
                    True,
                    {"Environment": "staging"},
                ),
                rds("reports-dev", "postgres", "16.3", "db.m6g.large", True),
                rds("orders-prod", "postgres", "16.3", "db.m6g.large", True, {"env": "production"}),
                rds("unbilled-test", "postgres", "16.3", "db.m6g.large", True, {"env": "test"}),
            ],
        )
    lines = [
        CostLine(
            charge_date=date(2026, 11, d),
            provider="aws",
            service_name="Amazon RDS",
            resource_id=f"arn:aws:rds:us-east-1:1:db:{name}",
            usage_type=usage,
            billed_cost=Decimal(cost),
            effective_cost=Decimal(cost),
            billing_currency="USD",
        )
        for d in range(1, 31)
        for name, usage, cost in (
            ("billed-mysql", "USE1-ExtendedSupport:Yr3:MySQL5.7", 19),
            ("billed-mysql", "USE1-InstanceUsage:db.m5.xl", 8),
            ("orders-staging", "InstanceUsage:db.m6g.l", 6),
            ("reports-dev", "InstanceUsage:db.m6g.l", 4),
            ("orders-prod", "InstanceUsage:db.m6g.l", 6),
        )
    ]
    with db.session() as s:
        replace_billing_period(
            s,
            org_id=org_id,
            source_kind="t",
            source_key="t",
            billing_period=date(2026, 11, 1),
            lines=lines,
        )
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        run(s, org_id, only=["aws.rds_extended_support", "aws.rds_multi_az_nonprod"])
        rows = {
            f.dedup_key.split(":db:")[-1] + "/" + f.detector: f for f in s.scalars(select(Finding))
        }
    hours = 24 * 30.4
    legacy = rows["legacy-pg/aws.rds_extended_support"]
    assert float(legacy.savings_monthly) == pytest.approx(
        8 * 2 * 0.20 * hours, abs=0.01
    )  # year 3, Multi-AZ
    assert (
        "postgres 11 (paid Extended Support) to 16" in legacy.title
        and "year 3 rate" in legacy.description
    )
    assert legacy.commands[0].startswith("aws rds create-blue-green-deployment")
    pg13 = rows["pg13/aws.rds_extended_support"]
    assert float(pg13.savings_monthly) == pytest.approx(2 * 0.10 * hours, abs=0.01)  # year 1
    billed = rows["billed-mysql/aws.rds_extended_support"]
    assert billed.evidence["basis"] == "billing" and float(billed.savings_monthly) == pytest.approx(
        19 * 30.4, abs=0.01
    )
    assert not any(
        k.startswith(("current/", "aurora/")) for k in rows if k.endswith("extended_support")
    )
    staging = rows["orders-staging/aws.rds_multi_az_nonprod"]
    assert staging.evidence["reason"] == "tag environment=staging"
    assert float(staging.savings_monthly) == pytest.approx(6 * 30.4 * 0.5, abs=0.01)
    assert rows["reports-dev/aws.rds_multi_az_nonprod"].evidence["reason"] == "name contains 'dev'"
    assert "orders-prod/aws.rds_multi_az_nonprod" not in rows  # production keeps Multi-AZ
    assert "unbilled-test/aws.rds_multi_az_nonprod" not in rows  # no bill line: no claim


def test_demo_flags_the_postgres_11_database(client: TestClient, db: Database) -> None:
    from costlens.demo.seed import ensure_demo, load_all_seeders

    load_all_seeders()
    with db.session() as s:
        demo_id = ensure_demo(s).id
    _, visitor = make_org(db, "Visitor", "visitor@x.com")
    add_to_org(db, demo_id, "visitor@x.com", Role.VIEWER)
    items = sign_in_client(client, db, visitor, demo_id).get("/api/v1/findings").json()["items"]
    ext = [i for i in items if i["detector"] == "aws.rds_extended_support"]
    assert len(ext) == 1 and "checkout-db from postgres 11" in ext[0]["title"]
    assert ext[0]["evidence"]["basis"] == "list price" and ext[0]["savings_monthly"] > 1000
