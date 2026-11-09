from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from costlens.accounts.roles import Role
from costlens.clouds.aws import metrics
from costlens.costs.ingest import CostLine, replace_billing_period
from costlens.db import Database
from costlens.db.base import utcnow
from costlens.db.tenant import set_tenant
from costlens.findings.aws_usage import rds_memory_gib, smaller
from costlens.findings.service import run
from costlens.inventory.aws import Found
from costlens.inventory.service import upsert
from costlens.models import Finding, ResourceMetric
from tests.helpers import add_to_org, make_org, sign_in_client


@pytest.mark.parametrize(
    ("current", "expected"),
    [
        ("m6i.xlarge", "m6i.large"),
        ("m5.large", None),
        ("t3.large", "t3.medium"),
        ("m6g.large", "m6g.medium"),
        ("t3.nano", None),
        ("c5.12xlarge", None),
        ("r6i.4xlarge", "r6i.2xlarge"),
        ("db.r6g.2xlarge", "db.r6g.xlarge"),
        ("db.t3.small", "db.t3.micro"),
        ("weird", None),
    ],
)
def test_size_ladder(current: str, expected: str | None) -> None:
    assert smaller(current) == expected


def test_rds_memory() -> None:
    assert rds_memory_gib("db.r6g.2xlarge") == 64 and rds_memory_gib("db.m5.large") == 8
    assert rds_memory_gib("db.t3.micro") == 1 and rds_memory_gib("db.z1d.large") is None


class FakeCloudWatch:
    """Hourly points for 3 days, split over two pages."""

    def __init__(self) -> None:
        self.calls = 0

    def get_metric_data(self, **kw):
        self.calls += 1
        start = datetime(2026, 11, 1, tzinfo=UTC)
        results = []
        for q in kw["MetricDataQueries"]:
            stat = q["MetricStat"]["Stat"]
            hours = range(0, 36) if "NextToken" not in kw else range(36, 72)
            values = [
                10.0 + (h % 24)
                if stat == "Average"
                else 30.0 + (h % 24)
                if stat == "Maximum"
                else 5.0
                for h in hours
            ]
            results.append(
                {
                    "Id": q["Id"],
                    "Timestamps": [start + timedelta(hours=h) for h in hours],
                    "Values": values,
                }
            )
        return {"MetricDataResults": results, **({} if "NextToken" in kw else {"NextToken": "p2"})}


def test_collect_daily_summaries(db: Database) -> None:
    org_id, _ = make_org(db, "Acme", "owner@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        upsert(
            s,
            org_id,
            "1",
            [
                Found(
                    "Amazon Elastic Compute Cloud",
                    "ec2_instance",
                    "i-1",
                    "us-east-1",
                    state="running",
                    attributes={"instance_type": "m6i.xlarge"},
                )
            ],
        )
        cw = FakeCloudWatch()
        written = metrics.collect(
            s,
            cw,
            org_id,
            metrics.running(s, "1", "us-east-1"),
            now=datetime(2026, 11, 4, tzinfo=UTC),
        )
        rows = {(r.metric, r.day): r for r in s.scalars(select(ResourceMetric))}
    assert cw.calls == 2 and written == 6  # cpu and memory x 3 days
    day1 = rows[("cpu_pct", date(2026, 11, 1))]
    assert day1.samples == 24 and day1.max == 53.0 and day1.min == 5.0
    assert day1.avg == pytest.approx(21.5) and day1.p95 == 52.0


def add_metrics(
    s, org_id, rid: str, metric: str, mx: float, days: int = 14, mn: float = 0.0
) -> None:
    for d in range(1, days + 1):
        s.add(
            ResourceMetric(
                org_id=org_id,
                resource_id=rid,
                metric=metric,
                day=utcnow().date() - timedelta(days=d),
                avg=mx / 2,
                max=mx,
                min=mn,
                p95=mx * 0.9,
                samples=24,
            )
        )


def test_ec2_and_rds_detectors(db: Database) -> None:
    org_id, _ = make_org(db, "Acme", "owner@acme.com")
    rds_arn = "arn:aws:rds:us-east-1:1:db:orders"
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        upsert(
            s,
            org_id,
            "1",
            [
                Found(
                    "Amazon Elastic Compute Cloud",
                    "ec2_instance",
                    rid,
                    "us-east-1",
                    name=rid,
                    state="running",
                    attributes={"instance_type": itype},
                )
                for rid, itype in (
                    ("i-idle", "m6i.large"),
                    ("i-big", "m6i.2xlarge"),
                    ("i-busy", "m6i.xlarge"),
                    ("i-mem", "m6i.xlarge"),
                    ("i-new", "m6i.xlarge"),
                    ("i-m5", "m5.large"),
                )
            ]
            + [
                Found(
                    "Amazon Relational Database Service",
                    "rds_instance",
                    rds_arn,
                    "us-east-1",
                    name="orders",
                    state="available",
                    attributes={"instance_class": "db.r6g.2xlarge"},
                ),
                Found(
                    "Amazon Relational Database Service",
                    "rds_instance",
                    "arn:aws:rds:us-east-1:1:db:old",
                    "us-east-1",
                    name="old",
                    state="available",
                    attributes={"instance_class": "db.m5.large"},
                ),
            ],
        )
        add_metrics(s, org_id, "i-idle", "cpu_pct", 3)
        add_metrics(s, org_id, "i-big", "cpu_pct", 30)
        add_metrics(s, org_id, "i-busy", "cpu_pct", 75)
        add_metrics(s, org_id, "i-mem", "cpu_pct", 20)
        add_metrics(s, org_id, "i-mem", "mem_pct", 70)
        add_metrics(s, org_id, "i-new", "cpu_pct", 10, days=3)  # not enough history
        add_metrics(s, org_id, "i-m5", "cpu_pct", 10)  # no smaller size in the family
        add_metrics(s, org_id, rds_arn, "cpu_pct", 25)
        add_metrics(s, org_id, rds_arn, "connections", 60)
        add_metrics(
            s, org_id, rds_arn, "free_memory_bytes", 50 * 2**30, mn=48 * 2**30
        )  # 64 GiB class
        add_metrics(s, org_id, "arn:aws:rds:us-east-1:1:db:old", "cpu_pct", 5)
        add_metrics(s, org_id, "arn:aws:rds:us-east-1:1:db:old", "connections", 0)
    lines = [
        CostLine(
            charge_date=date(2026, 11, d),
            provider="aws",
            service_name=svc,
            resource_id=rid,
            billed_cost=Decimal(10),
            effective_cost=Decimal(10),
            billing_currency="USD",
        )
        for d in range(1, 31)
        for rid, svc in [
            (r, "Amazon Elastic Compute Cloud")
            for r in ("i-idle", "i-big", "i-busy", "i-mem", "i-new", "i-m5")
        ]
        + [(rds_arn, "Amazon RDS"), ("arn:aws:rds:us-east-1:1:db:old", "Amazon RDS")]
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
        run(s, org_id, only=["aws.ec2_rightsizing", "aws.rds_rightsizing"])
        rows = {f.dedup_key: f for f in s.scalars(select(Finding))}
    monthly = 10 * 30.4
    assert float(rows["aws.ec2_rightsizing:idle:i-idle"].savings_monthly) == pytest.approx(
        monthly, abs=0.01
    )
    big = rows["aws.ec2_rightsizing:size:i-big"]
    assert "m6i.2xlarge to m6i.xlarge" in big.title and float(big.savings_monthly) == pytest.approx(
        monthly / 2, abs=0.01
    )
    assert "memory not measured" in big.description and any(
        "--instance-type m6i.xlarge" in c for c in big.commands
    )
    for absent in ("size:i-busy", "size:i-mem", "size:i-new", "size:i-m5", "idle:i-busy"):
        assert f"aws.ec2_rightsizing:{absent}" not in rows
    orders = rows[f"aws.rds_rightsizing:size:{rds_arn}"]
    assert (
        "db.r6g.2xlarge to db.r6g.xlarge" in orders.title
        and orders.evidence["memory_used_max_pct"] == 25.0
    )
    idle_db = rows["aws.rds_rightsizing:idle:arn:aws:rds:us-east-1:1:db:old"]
    assert (
        idle_db.risk == "high" and "final-db-snapshot-identifier old-final" in idle_db.commands[0]
    )


def test_inventory_collection_gathers_metrics_and_survives_denial(
    client: TestClient, db: Database
) -> None:
    from costlens.inventory.service import collect_aws
    from costlens.models import CloudConnection
    from tests.fakes.aws import FakeAws
    from tests.test_inventory import FakeEC2

    org_id, _ = make_org(db, "Acme", "o@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        conn = CloudConnection(
            org_id=org_id,
            provider="aws",
            name="P",
            status="active",
            external_id="ext",
            role_arn="arn:aws:iam::222222222222:role/x",
            account_id="222222222222",
        )
        s.add(conn)
        s.flush()
        conn_id = conn.id

    class Denied:
        def get_metric_data(self, **_):
            raise RuntimeError("AccessDenied")

    for cw, expect_metrics in ((FakeCloudWatch(), True), (Denied(), False)):
        aws = FakeAws(clients={"ec2": FakeEC2(), "cloudwatch": cw})
        aws.allow("arn:aws:iam::222222222222:role/x", "ext")
        with db.session() as s:
            counts = collect_aws(s, aws, conn_id)
        assert counts["ec2_instances"] == 2  # inventory always collected
        assert (counts.get("metrics", 0) > 0) is expect_metrics


def test_demo_suggests_downsizing_the_quiet_web_server(client: TestClient, db: Database) -> None:
    from costlens.demo.seed import ensure_demo, load_all_seeders

    load_all_seeders()
    with db.session() as s:
        demo_id = ensure_demo(s).id
    _, visitor = make_org(db, "Visitor", "visitor@x.com")
    add_to_org(db, demo_id, "visitor@x.com", Role.VIEWER)
    items = sign_in_client(client, db, visitor, demo_id).get("/api/v1/findings").json()["items"]
    sizes = [i for i in items if i["detector"] == "aws.ec2_rightsizing"]
    assert (
        len(sizes) == 1
        and "checkout-web-1" in sizes[0]["title"]
        and "to m6i.large" in sizes[0]["title"]
    )
    assert not any(
        i["detector"] == "aws.rds_rightsizing" for i in items
    )  # no bill line: needs more data
