"""CloudWatch usage for running EC2 and RDS instances (read-only GetMetricData).

Hourly points for the last 14 days become one row per resource, metric and day:
average, highest hourly maximum, lowest hourly minimum and the 95th percentile of
hourly maxima. Memory for EC2 exists only when the CloudWatch agent publishes it."""

from __future__ import annotations

import math
import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from costlens.db.base import utcnow
from costlens.models import CloudResource, ResourceMetric

DAYS = 14
BATCH = 400  # GetMetricData allows 500 queries per call

# (our name, namespace, metric, dimension, statistic set)
EC2 = [
    ("cpu_pct", "AWS/EC2", "CPUUtilization", "InstanceId"),
    ("mem_pct", "CWAgent", "mem_used_percent", "InstanceId"),
]
RDS = [
    ("cpu_pct", "AWS/RDS", "CPUUtilization", "DBInstanceIdentifier"),
    ("free_memory_bytes", "AWS/RDS", "FreeableMemory", "DBInstanceIdentifier"),
    ("connections", "AWS/RDS", "DatabaseConnections", "DBInstanceIdentifier"),
    ("read_iops", "AWS/RDS", "ReadIOPS", "DBInstanceIdentifier"),
    ("write_iops", "AWS/RDS", "WriteIOPS", "DBInstanceIdentifier"),
    ("disk_queue", "AWS/RDS", "DiskQueueDepth", "DBInstanceIdentifier"),
    ("swap_bytes", "AWS/RDS", "SwapUsage", "DBInstanceIdentifier"),
]
CACHE = [
    ("cache_hit_rate", "AWS/ElastiCache", "CacheHitRate", "CacheClusterId"),
    ("cache_memory_pct", "AWS/ElastiCache", "DatabaseMemoryUsagePercentage", "CacheClusterId"),
    ("evictions", "AWS/ElastiCache", "Evictions", "CacheClusterId"),
    ("engine_cpu", "AWS/ElastiCache", "EngineCPUUtilization", "CacheClusterId"),
    ("connections", "AWS/ElastiCache", "CurrConnections", "CacheClusterId"),
]


def _queries(
    resources: list[CloudResource],
) -> tuple[list[dict[str, Any]], dict[str, tuple[str, str, str]]]:
    queries, index = [], {}
    for r in resources:
        if r.resource_type == "sagemaker_endpoint":
            jobs = [
                (
                    "invocations",
                    "AWS/SageMaker",
                    "Invocations",
                    [
                        {"Name": "EndpointName", "Value": r.name or ""},
                        {"Name": "VariantName", "Value": v},
                    ],
                )
                for v in (r.attributes or {}).get("variants") or ["AllTraffic"]
            ]
        else:
            if r.resource_type == "ec2_instance":
                specs, dim_value = EC2, r.resource_id
            elif r.resource_type == "elasticache_cluster":
                specs, dim_value = CACHE, r.name or r.resource_id.rsplit(":", 1)[-1]
            else:
                specs, dim_value = RDS, r.name or r.resource_id.rsplit(":", 1)[-1]
            jobs = [
                (name, namespace, metric, [{"Name": dim, "Value": dim_value}])
                for name, namespace, metric, dim in specs
            ]
        for name, namespace, metric, dims in jobs:
            for stat in ("Average", "Maximum", "Minimum"):
                qid = f"q{len(queries)}"
                index[qid] = (r.resource_id, name, stat)
                queries.append(
                    {
                        "Id": qid,
                        "ReturnData": True,
                        "MetricStat": {
                            "Metric": {
                                "Namespace": namespace,
                                "MetricName": metric,
                                "Dimensions": dims,
                            },
                            "Period": 3600,
                            "Stat": stat,
                        },
                    }
                )
    return queries, index


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1)]


def collect(
    db: Session,
    client: Any,
    org_id: uuid.UUID,
    resources: list[CloudResource],
    now: datetime | None = None,
) -> int:
    """Fetch and store daily summaries; returns rows written."""
    now = now or utcnow()
    start = (now - timedelta(days=DAYS)).replace(minute=0, second=0, microsecond=0)
    queries, index = _queries(resources)
    points: dict[tuple[str, str, str], dict[datetime, float]] = defaultdict(dict)
    for i in range(0, len(queries), BATCH):
        token = None
        while True:
            args: dict[str, Any] = {
                "MetricDataQueries": queries[i : i + BATCH],
                "StartTime": start,
                "EndTime": now,
                "ScanBy": "TimestampAscending",
            }
            if token:
                args["NextToken"] = token
            page = client.get_metric_data(**args)
            for result in page.get("MetricDataResults", []):
                key = index.get(result.get("Id", ""))
                if key:
                    for ts, v in zip(
                        result.get("Timestamps", []), result.get("Values", []), strict=False
                    ):
                        at = ts if ts.tzinfo else ts.replace(tzinfo=UTC)
                        # several variants of one endpoint: keep the busiest per hour
                        points[key][at] = max(points[key].get(at, float(v)), float(v))
            token = page.get("NextToken")
            if not token:
                break
    # Group by resource / metric / day.
    days: dict[tuple[str, str, date], dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for (rid, name, stat), series in points.items():
        for ts, v in series.items():
            days[(rid, name, ts.date())][stat].append(v)
    ids = {r.resource_id for r in resources}
    db.execute(
        delete(ResourceMetric).where(
            ResourceMetric.resource_id.in_(ids), ResourceMetric.day >= start.date()
        )
    )
    written = 0
    for (rid, name, day), stats in days.items():
        avg, mx, mn = stats.get("Average", []), stats.get("Maximum", []), stats.get("Minimum", [])
        if not avg and not mx:
            continue
        maxima = mx or avg
        db.add(
            ResourceMetric(
                org_id=org_id,
                resource_id=rid,
                metric=name,
                day=day,
                avg=sum(avg or maxima) / len(avg or maxima),
                max=max(maxima),
                min=min(mn or avg or maxima),
                p95=_p95(maxima),
                samples=len(maxima),
            )
        )
        written += 1
    db.flush()
    return written


def running(db: Session, account_id: str | None, region: str) -> list[CloudResource]:
    return list(
        db.scalars(
            select(CloudResource).where(
                CloudResource.provider == "aws",
                CloudResource.account_id == account_id,
                CloudResource.region == region,
                CloudResource.resource_type.in_(
                    ("ec2_instance", "rds_instance", "sagemaker_endpoint", "elasticache_cluster")
                ),
                CloudResource.state.in_(("running", "available", "InService")),
            )
        )
    )
