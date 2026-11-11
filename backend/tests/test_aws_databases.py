from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select

from ccradar.aws_audit.service import run_audit
from ccradar.costs.ingest import CostLine, replace_billing_period
from ccradar.db import Database
from ccradar.db.base import utcnow
from ccradar.db.tenant import set_tenant
from ccradar.findings.aws_databases import aurora_class, dynamodb_class
from ccradar.findings.service import run
from ccradar.inventory import aws_databases as dbc
from ccradar.inventory.aws import Found
from ccradar.inventory.service import upsert
from ccradar.models import AuditResult, Finding
from tests.helpers import make_org

CLUSTER = "arn:aws:rds:us-east-1:1:cluster:orders"


class Fake:
    def describe_db_clusters(self, **kw: Any) -> dict[str, Any]:
        return {
            "DBClusters": [
                {
                    "DBClusterArn": CLUSTER,
                    "DBClusterIdentifier": "orders",
                    "Engine": "aurora-postgresql",
                    "Status": "available",
                    "DBClusterMembers": [{"DBInstanceIdentifier": "orders-1"}],
                    "ServerlessV2ScalingConfiguration": {"MinCapacity": 4, "MaxCapacity": 16},
                }
            ]
        }

    def list_tables(self, **kw: Any) -> dict[str, Any]:
        if "ExclusiveStartTableName" not in kw:
            return {"TableNames": ["a"], "LastEvaluatedTableName": "a"}
        return {"TableNames": ["b"]}

    def describe_table(self, TableName: str) -> dict[str, Any]:
        return {
            "Table": {
                "TableArn": f"arn:ddb:{TableName}",
                "TableStatus": "ACTIVE",
                "BillingModeSummary": {"BillingMode": "PAY_PER_REQUEST"}
                if TableName == "b"
                else None,
            }
        }

    def describe_cache_clusters(self, **kw: Any) -> dict[str, Any]:
        return {
            "CacheClusters": [
                {
                    "ARN": "arn:cache:c",
                    "CacheClusterId": "c",
                    "CacheNodeType": "cache.r4.large",
                    "NumCacheNodes": 2,
                }
            ]
        }

    def list_domain_names(self) -> dict[str, Any]:
        return {"DomainNames": [{"DomainName": f"d{i}"} for i in range(7)]}

    def describe_domains(self, DomainNames: list[str]) -> dict[str, Any]:
        assert len(DomainNames) <= 5
        return {
            "DomainStatusList": [
                {
                    "DomainName": n,
                    "ARN": f"arn:es:{n}",
                    "ClusterConfig": {"InstanceType": "m4.large.search"},
                    "EBSOptions": {"VolumeType": "gp2"},
                }
                for n in DomainNames
            ]
        }

    def describe_clusters(self, **kw: Any) -> dict[str, Any]:
        return {
            "Clusters": [
                {"ClusterIdentifier": "wh", "ClusterStatus": "paused", "NodeType": "ra3.4xlarge"}
            ]
        }

    def describe_replication_tasks(self, **kw: Any) -> dict[str, Any]:
        return {
            "ReplicationTasks": [
                {"ReplicationInstanceArn": "arn:dms:1", "Status": "stopped"},
                {"ReplicationInstanceArn": "arn:dms:2", "Status": "running"},
            ]
        }

    def describe_replication_instances(self, **kw: Any) -> dict[str, Any]:
        return {
            "ReplicationInstances": [
                {
                    "ReplicationInstanceArn": f"arn:dms:{i}",
                    "ReplicationInstanceIdentifier": f"r{i}",
                    "ReplicationInstanceStatus": "available",
                }
                for i in (1, 2)
            ]
        }


def test_collectors() -> None:
    f = Fake()
    (cluster,) = dbc.db_clusters(f, "us-east-1")
    assert cluster.attributes["member_arns"] == ["arn:aws:rds:us-east-1:1:db:orders-1"]
    assert (
        cluster.attributes["storage_type"] == "aurora"
        and cluster.attributes["serverless_min_acu"] == 4
    )
    tables = {t.name: t.attributes for t in dbc.dynamodb_tables(f, "r")}
    assert (
        tables["a"]["billing_mode"] == "PROVISIONED"
        and tables["b"]["billing_mode"] == "PAY_PER_REQUEST"
    )
    assert tables["a"]["table_class"] == "STANDARD"
    (cache,) = dbc.elasticache_clusters(f, "r")
    assert cache.attributes["node_type"] == "cache.r4.large"
    assert len(list(dbc.opensearch_domains(f, "r"))) == 7
    (wh,) = dbc.redshift_clusters(f, "r")
    assert wh.state == "paused"
    dms = {r.name: r.attributes for r in dbc.dms_instances(f, "r")}
    assert dms["r1"]["running_tasks"] == 0 and dms["r2"]["running_tasks"] == 1


@pytest.mark.parametrize(
    ("usage", "aurora", "dynamo"),
    [
        ("USE1-Aurora:StorageIOUsage", "io", "other"),
        ("USE1-Aurora:StorageUsage", "storage", "other"),
        ("USE1-InstanceUsage:db.r6g.large", "instance", "other"),
        ("TimedStorage-ByteHrs", "other", "storage"),
        ("ReadRequestUnits", "other", "throughput"),
        ("WriteCapacityUnit-Hrs", "other", "throughput"),
    ],
)
def test_classifiers(usage: str, aurora: str, dynamo: str) -> None:
    assert aurora_class(usage) == aurora and dynamodb_class(usage) == dynamo


def _seed(db: Database):
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    rds = "Amazon Relational Database Service"
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        upsert(
            s,
            org_id,
            "1",
            [
                Found(
                    rds,
                    "rds_cluster",
                    CLUSTER,
                    "us-east-1",
                    name="orders",
                    state="available",
                    attributes={
                        "engine": "aurora-postgresql",
                        "storage_type": "aurora",
                        "member_arns": ["arn:db:orders-1"],
                        "members": ["orders-1"],
                    },
                ),
                Found(
                    rds,
                    "rds_cluster",
                    "arn:cluster:dev-api",
                    "us-east-1",
                    name="dev-api",
                    state="available",
                    attributes={
                        "engine": "aurora-mysql",
                        "storage_type": "aurora-iopt1",
                        "serverless_min_acu": 4,
                        "members": ["a"],
                    },
                ),
                Found(
                    rds,
                    "rds_cluster",
                    "arn:cluster:graph-test",
                    "us-east-1",
                    name="graph-test",
                    state="available",
                    attributes={"engine": "neptune", "members": ["a", "b"]},
                ),
                Found(
                    rds,
                    "rds_instance",
                    "arn:db:ledger",
                    "us-east-1",
                    name="ledger",
                    state="available",
                    attributes={"engine": "oracle-ee", "license_model": "license-included"},
                ),
                Found(
                    "Amazon DynamoDB",
                    "dynamodb_table",
                    "arn:ddb:events",
                    "us-east-1",
                    name="events",
                    state="active",
                    attributes={"table_class": "STANDARD", "billing_mode": "PROVISIONED"},
                ),
                Found(
                    "Amazon DynamoDB",
                    "dynamodb_table",
                    "arn:ddb:hot",
                    "us-east-1",
                    name="hot",
                    state="active",
                    attributes={"table_class": "STANDARD", "billing_mode": "PAY_PER_REQUEST"},
                ),
                Found(
                    "AWS Database Migration Service",
                    "dms_replication_instance",
                    "arn:dms:old",
                    "us-east-1",
                    name="old",
                    state="available",
                    attributes={"tasks": 1, "running_tasks": 0},
                ),
                Found(
                    "Amazon ElastiCache",
                    "elasticache_cluster",
                    "arn:cache:old",
                    "us-east-1",
                    name="old",
                    state="available",
                    attributes={"node_type": "cache.r4.xlarge"},
                ),
                Found(
                    "Amazon ElastiCache",
                    "elasticache_cluster",
                    "arn:cache:x86",
                    "us-east-1",
                    name="x86",
                    state="available",
                    attributes={"node_type": "cache.r6i.large"},
                ),
                Found(
                    "Amazon OpenSearch Service",
                    "opensearch_domain",
                    "arn:es:logs-dev",
                    "us-east-1",
                    name="logs-dev",
                    state="active",
                    attributes={
                        "volume_type": "gp2",
                        "instance_type": "r6g.large.search",
                        "dedicated_master": True,
                    },
                ),
                Found(
                    "Amazon Redshift",
                    "redshift_cluster",
                    "arn:rs:bi-staging",
                    "us-east-1",
                    name="bi-staging",
                    state="available",
                    attributes={"node_type": "dc1.large"},
                ),
            ],
        )
    today = utcnow().date()
    rows = [
        (CLUSTER, "Aurora:StorageIOUsage", 10.0),
        (CLUSTER, "Aurora:StorageUsage", 1.0),
        ("arn:db:orders-1", "InstanceUsage:db.r6g.large", 10.0),
        ("arn:ddb:events", "TimedStorage-ByteHrs", 5.0),
        ("arn:ddb:events", "ReadCapacityUnit-Hrs", 2.0),
        ("arn:ddb:hot", "ReadRequestUnits", 5.0),
        ("arn:ddb:hot", "TimedStorage-ByteHrs", 0.1),
        ("arn:dms:old", "InstanceUsage", 3.0),
        ("arn:cache:old", "NodeUsage", 6.0),
    ]
    lines = [
        CostLine(
            charge_date=today - timedelta(days=d),
            provider="aws",
            service_name="svc",
            resource_id=rid,
            usage_type=usage,
            billed_cost=Decimal(str(c)),
            effective_cost=Decimal(str(c)),
            billing_currency="USD",
        )
        for d in range(1, 31)
        for rid, usage, c in rows
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


def test_database_detectors_and_audit(db: Database) -> None:
    org_id = _seed(db)
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        run(s, org_id)
        rows = {f.dedup_key: f for f in s.scalars(select(Finding))}
        audit = run_audit(s, org_id, run_detectors=False)
        checks = {
            r.check_id: r
            for r in s.scalars(select(AuditResult).where(AuditResult.run_id == audit.id))
        }
    m = 30.4
    aurora = rows[f"aws.aurora_io_optimized:{CLUSTER}"]
    assert float(aurora.savings_monthly) == pytest.approx((10 - 0.3 * 10 - 1.25 * 1) * m, abs=0.02)
    assert "aws.aurora_io_optimized:arn:cluster:dev-api" not in rows  # already I/O-Optimized
    events = rows["aws.dynamodb_table_class:arn:ddb:events"]
    assert float(events.savings_monthly) == pytest.approx((5 * 0.6 - 2 * 0.25) * m, abs=0.02)
    assert "aws.dynamodb_table_class:arn:ddb:hot" not in rows  # throughput-heavy: IA costs more
    assert float(rows["aws.dms_idle:arn:dms:old"].savings_monthly) == pytest.approx(3 * m, abs=0.02)
    cache = rows["aws.elasticache_previous_gen:arn:cache:old"]
    assert "cache.r7g.xlarge" in cache.title and float(cache.savings_monthly) == pytest.approx(
        6 * m * 0.1, abs=0.02
    )

    for check in ("aurora.io", "dynamodb.table_class", "dms.tasks", "elasticache.nodes"):
        assert checks[check].status == "saving", check
    assert "ledger" in checks["rds.licensing"].reason
    assert "dev-api" in checks["aurora.serverless"].reason
    assert "hot" in checks["dynamodb.capacity"].reason  # 152/month on-demand throughput
    assert (
        "gp2" in checks["opensearch.domains"].reason
        and "dedicated masters" in checks["opensearch.domains"].reason
    )
    redshift = checks["redshift.clusters"].reason
    assert "bi-staging" in redshift and "retired node types" in redshift
    assert "graph-test" in checks["docdb.neptune"].reason
