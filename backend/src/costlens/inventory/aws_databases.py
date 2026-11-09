"""Database inventory: Aurora / DocumentDB / Neptune clusters (one RDS API), DynamoDB
tables, ElastiCache, OpenSearch, Redshift and DMS (read-only Describe/List calls)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from costlens.inventory.aws import Found, _pages, _tags

MAX_CHILDREN = 500


def db_clusters(client: Any, region: str) -> Iterator[Found]:
    for page in _pages(client.describe_db_clusters, "Marker", "Marker", MaxRecords=100):
        for c in page.get("DBClusters", []):
            arn = c["DBClusterArn"]
            members = [m.get("DBInstanceIdentifier") for m in c.get("DBClusterMembers") or []]
            v2 = c.get("ServerlessV2ScalingConfiguration") or {}
            yield Found(
                "Amazon Relational Database Service",
                "rds_cluster",
                arn,
                region,
                name=c.get("DBClusterIdentifier"),
                state=c.get("Status"),
                created_at=c.get("ClusterCreateTime"),
                tags=_tags(c.get("TagList")),
                attributes={
                    "engine": c.get("Engine"),  # aurora-mysql | aurora-postgresql | docdb | neptune
                    "engine_version": c.get("EngineVersion"),
                    "storage_type": c.get("StorageType")
                    or "aurora",  # aurora-iopt1 = I/O-Optimized
                    "members": members,
                    "member_arns": [
                        arn.replace(":cluster:", ":db:").rsplit(":", 1)[0] + f":{m}"
                        for m in members
                    ],
                    "serverless_min_acu": v2.get("MinCapacity"),
                    "serverless_max_acu": v2.get("MaxCapacity"),
                    "multi_az": c.get("MultiAZ"),
                },
            )


def dynamodb_tables(client: Any, region: str) -> Iterator[Found]:
    names = [
        n
        for page in _pages(
            client.list_tables, "ExclusiveStartTableName", "LastEvaluatedTableName", Limit=100
        )
        for n in page.get("TableNames", [])
    ]
    for name in names[:MAX_CHILDREN]:
        t = client.describe_table(TableName=name).get("Table") or {}
        billing = (t.get("BillingModeSummary") or {}).get("BillingMode") or "PROVISIONED"
        thr = t.get("ProvisionedThroughput") or {}
        yield Found(
            "Amazon DynamoDB",
            "dynamodb_table",
            t.get("TableArn") or name,
            region,
            name=name,
            state=str(t.get("TableStatus") or "").lower() or None,
            created_at=t.get("CreationDateTime"),
            attributes={
                "billing_mode": billing,
                "table_class": (t.get("TableClassSummary") or {}).get("TableClass") or "STANDARD",
                "read_units": thr.get("ReadCapacityUnits"),
                "write_units": thr.get("WriteCapacityUnits"),
                "size_bytes": t.get("TableSizeBytes"),
                "items": t.get("ItemCount"),
                "indexes": len(t.get("GlobalSecondaryIndexes") or []),
            },
        )


def elasticache_clusters(client: Any, region: str) -> Iterator[Found]:
    for page in _pages(client.describe_cache_clusters, "Marker", "Marker", MaxRecords=100):
        for c in page.get("CacheClusters", []):
            yield Found(
                "Amazon ElastiCache",
                "elasticache_cluster",
                c.get("ARN") or c["CacheClusterId"],
                region,
                name=c.get("CacheClusterId"),
                state=c.get("CacheClusterStatus"),
                created_at=c.get("CacheClusterCreateTime"),
                attributes={
                    "node_type": c.get("CacheNodeType"),
                    "engine": c.get("Engine"),
                    "engine_version": c.get("EngineVersion"),
                    "nodes": c.get("NumCacheNodes"),
                    "replication_group": c.get("ReplicationGroupId"),
                },
            )


def opensearch_domains(client: Any, region: str) -> Iterator[Found]:
    names = [d["DomainName"] for d in client.list_domain_names().get("DomainNames", [])][
        :MAX_CHILDREN
    ]
    for i in range(0, len(names), 5):  # DescribeDomains takes 5 at a time
        for d in client.describe_domains(DomainNames=names[i : i + 5]).get("DomainStatusList", []):
            cfg = d.get("ClusterConfig") or {}
            ebs = d.get("EBSOptions") or {}
            yield Found(
                "Amazon OpenSearch Service",
                "opensearch_domain",
                d.get("ARN") or d["DomainName"],
                region,
                name=d.get("DomainName"),
                state="deleted" if d.get("Deleted") else "active",
                attributes={
                    "engine_version": d.get("EngineVersion"),
                    "instance_type": cfg.get("InstanceType"),
                    "instances": cfg.get("InstanceCount"),
                    "dedicated_master": bool(cfg.get("DedicatedMasterEnabled")),
                    "master_type": cfg.get("DedicatedMasterType"),
                    "warm_enabled": bool(cfg.get("WarmEnabled")),
                    "zone_awareness": bool(cfg.get("ZoneAwarenessEnabled")),
                    "volume_type": ebs.get("VolumeType"),
                    "volume_gb": ebs.get("VolumeSize"),
                },
            )


def redshift_clusters(client: Any, region: str) -> Iterator[Found]:
    for page in _pages(client.describe_clusters, "Marker", "Marker", MaxRecords=100):
        for c in page.get("Clusters", []):
            yield Found(
                "Amazon Redshift",
                "redshift_cluster",
                c.get("ClusterNamespaceArn") or c["ClusterIdentifier"],
                region,
                name=c.get("ClusterIdentifier"),
                state=c.get("ClusterStatus"),  # available | paused | ...
                created_at=c.get("ClusterCreateTime"),
                tags=_tags(c.get("Tags")),
                attributes={
                    "node_type": c.get("NodeType"),
                    "nodes": c.get("NumberOfNodes"),
                    "multi_az": str(c.get("MultiAZ") or "").lower() == "enabled",
                },
            )


def dms_instances(client: Any, region: str) -> Iterator[Found]:
    tasks: dict[str, list[str]] = {}
    for page in _pages(client.describe_replication_tasks, "Marker", "Marker", MaxRecords=100):
        for t in page.get("ReplicationTasks", []):
            tasks.setdefault(t.get("ReplicationInstanceArn", ""), []).append(t.get("Status", ""))
    for page in _pages(client.describe_replication_instances, "Marker", "Marker", MaxRecords=100):
        for r in page.get("ReplicationInstances", []):
            arn = r["ReplicationInstanceArn"]
            statuses = tasks.get(arn, [])
            yield Found(
                "AWS Database Migration Service",
                "dms_replication_instance",
                arn,
                region,
                name=r.get("ReplicationInstanceIdentifier"),
                state=r.get("ReplicationInstanceStatus"),
                created_at=r.get("InstanceCreateTime"),
                attributes={
                    "instance_class": r.get("ReplicationInstanceClass"),
                    "multi_az": r.get("MultiAZ"),
                    "tasks": len(statuses),
                    "running_tasks": sum(1 for s in statuses if s in ("running", "starting")),
                },
            )


DATABASE_COLLECTORS = [
    ("rds", db_clusters),
    ("dynamodb", dynamodb_tables),
    ("elasticache", elasticache_clusters),
    ("opensearch", opensearch_domains),
    ("redshift", redshift_clusters),
    ("dms", dms_instances),
]
DATABASE_TYPES = {
    "db_clusters": ("rds_cluster",),
    "dynamodb_tables": ("dynamodb_table",),
    "elasticache_clusters": ("elasticache_cluster",),
    "opensearch_domains": ("opensearch_domain",),
    "redshift_clusters": ("redshift_cluster",),
    "dms_instances": ("dms_replication_instance",),
}
