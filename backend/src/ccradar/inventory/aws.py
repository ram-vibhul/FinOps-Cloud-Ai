"""Collect cost-driving AWS resources (read-only ``Describe*`` calls).

Collectors return :class:`Found` records; ``resource_id`` uses the identifier that
appears in AWS billing data (instance id, volume id, ARN for RDS, load balancers and
NAT gateways, allocation id for addresses), so inventory and costs join directly.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

log = logging.getLogger(__name__)
MAX_PAGES = 20


@dataclass
class Found:
    service: str
    resource_type: str
    resource_id: str
    region: str
    name: str | None = None
    state: str | None = None
    created_at: datetime | None = None
    tags: dict[str, str] = field(default_factory=dict)
    attributes: dict[str, Any] = field(default_factory=dict)


def _tags(items: list[dict[str, Any]] | None) -> dict[str, str]:
    return {t.get("Key", ""): t.get("Value", "") for t in (items or []) if t.get("Key")}


def _pages(
    call: Callable[..., dict[str, Any]], token_in: str, token_out: str, **args: Any
) -> Iterator[dict[str, Any]]:
    token = None
    for _ in range(MAX_PAGES):
        page = call(**({**args, token_in: token} if token else args))
        yield page
        token = page.get(token_out)
        if not token:
            return


def ec2_instances(ec2: Any, region: str) -> Iterator[Found]:
    for page in _pages(ec2.describe_instances, "NextToken", "NextToken", MaxResults=1000):
        for reservation in page.get("Reservations", []):
            for i in reservation.get("Instances", []):
                tags = _tags(i.get("Tags"))
                yield Found(
                    "Amazon Elastic Compute Cloud",
                    "ec2_instance",
                    i["InstanceId"],
                    region,
                    name=tags.get("Name"),
                    state=(i.get("State") or {}).get("Name"),
                    created_at=i.get("LaunchTime"),
                    tags=tags,
                    attributes={
                        "instance_type": i.get("InstanceType"),
                        "platform": i.get("PlatformDetails"),
                        "lifecycle": i.get("InstanceLifecycle", "on-demand"),
                        "architecture": i.get("Architecture"),
                    },
                )


def ebs_volumes(ec2: Any, region: str) -> Iterator[Found]:
    for page in _pages(ec2.describe_volumes, "NextToken", "NextToken", MaxResults=500):
        for v in page.get("Volumes", []):
            tags = _tags(v.get("Tags"))
            yield Found(
                "Amazon Elastic Compute Cloud",
                "ebs_volume",
                v["VolumeId"],
                region,
                name=tags.get("Name"),
                state=v.get("State"),
                created_at=v.get("CreateTime"),
                tags=tags,
                attributes={
                    "size_gb": v.get("Size"),
                    "volume_type": v.get("VolumeType"),
                    "iops": v.get("Iops"),
                    "attached_to": [a.get("InstanceId") for a in v.get("Attachments", [])],
                },
            )


def elastic_ips(ec2: Any, region: str) -> Iterator[Found]:
    for a in ec2.describe_addresses().get("Addresses", []):
        tags = _tags(a.get("Tags"))
        attached = bool(a.get("AssociationId"))
        yield Found(
            "Amazon Virtual Private Cloud",
            "elastic_ip",
            a.get("AllocationId") or a.get("PublicIp"),
            region,
            name=tags.get("Name") or a.get("PublicIp"),
            state="in-use" if attached else "unattached",
            tags=tags,
            attributes={"public_ip": a.get("PublicIp"), "instance": a.get("InstanceId")},
        )


def nat_gateways(ec2: Any, region: str) -> Iterator[Found]:
    for page in _pages(ec2.describe_nat_gateways, "NextToken", "NextToken", MaxResults=1000):
        for n in page.get("NatGateways", []):
            tags = _tags(n.get("Tags"))
            yield Found(
                "Amazon Virtual Private Cloud",
                "nat_gateway",
                n["NatGatewayId"],
                region,
                name=tags.get("Name"),
                state=n.get("State"),
                created_at=n.get("CreateTime"),
                tags=tags,
                attributes={"vpc": n.get("VpcId"), "subnet": n.get("SubnetId")},
            )


def snapshots(ec2: Any, region: str) -> Iterator[Found]:
    for page in _pages(
        ec2.describe_snapshots, "NextToken", "NextToken", OwnerIds=["self"], MaxResults=1000
    ):
        for s in page.get("Snapshots", []):
            tags = _tags(s.get("Tags"))
            yield Found(
                "Amazon Elastic Compute Cloud",
                "ebs_snapshot",
                s["SnapshotId"],
                region,
                name=tags.get("Name") or s.get("Description"),
                state=s.get("State"),
                created_at=s.get("StartTime"),
                tags=tags,
                attributes={"size_gb": s.get("VolumeSize"), "volume": s.get("VolumeId")},
            )


def load_balancers(elb: Any, region: str) -> Iterator[Found]:
    for page in _pages(elb.describe_load_balancers, "Marker", "NextMarker", PageSize=400):
        for lb in page.get("LoadBalancers", []):
            yield Found(
                "Elastic Load Balancing",
                "load_balancer",
                lb["LoadBalancerArn"],
                region,
                name=lb.get("LoadBalancerName"),
                state=(lb.get("State") or {}).get("Code"),
                created_at=lb.get("CreatedTime"),
                attributes={"type": lb.get("Type"), "scheme": lb.get("Scheme")},
            )


def rds_instances(rds: Any, region: str) -> Iterator[Found]:
    for page in _pages(rds.describe_db_instances, "Marker", "Marker", MaxRecords=100):
        for d in page.get("DBInstances", []):
            tags = _tags(d.get("TagList"))
            yield Found(
                "Amazon Relational Database Service",
                "rds_instance",
                d["DBInstanceArn"],
                region,
                name=d.get("DBInstanceIdentifier"),
                state=d.get("DBInstanceStatus"),
                created_at=d.get("InstanceCreateTime"),
                tags=tags,
                attributes={
                    "instance_class": d.get("DBInstanceClass"),
                    "engine": d.get("Engine"),
                    "engine_version": d.get("EngineVersion"),
                    "multi_az": d.get("MultiAZ"),
                    "storage_gb": d.get("AllocatedStorage"),
                    "storage_type": d.get("StorageType"),
                    "iops": d.get("Iops"),
                    "license_model": d.get("LicenseModel"),
                    "cluster": d.get("DBClusterIdentifier"),
                },
            )


COLLECTORS: list[tuple[str, Callable[[Any, str], Iterator[Found]]]] = [
    ("ec2", ec2_instances),
    ("ec2", ebs_volumes),
    ("ec2", elastic_ips),
    ("ec2", nat_gateways),
    ("ec2", snapshots),
    ("elbv2", load_balancers),
    ("rds", rds_instances),
]


def _compute() -> None:
    from ccradar.inventory.aws_analytics import ANALYTICS_COLLECTORS
    from ccradar.inventory.aws_compute import COMPUTE_COLLECTORS
    from ccradar.inventory.aws_databases import DATABASE_COLLECTORS
    from ccradar.inventory.aws_platform import PLATFORM_COLLECTORS
    from ccradar.inventory.aws_storage import STORAGE_COLLECTORS

    COLLECTORS.extend(
        COMPUTE_COLLECTORS
        + STORAGE_COLLECTORS
        + DATABASE_COLLECTORS
        + ANALYTICS_COLLECTORS
        + PLATFORM_COLLECTORS
    )


_compute()

# Resource types each collector produces (a failed collector keeps its types untouched).
COLLECTOR_TYPES: dict[str, tuple[str, ...]] = {
    "ec2_instances": ("ec2_instance",),
    "ebs_volumes": ("ebs_volume",),
    "elastic_ips": ("elastic_ip",),
    "nat_gateways": ("nat_gateway",),
    "snapshots": ("ebs_snapshot",),
    "load_balancers": ("load_balancer",),
    "rds_instances": ("rds_instance",),
    "lambda_functions": ("lambda_function",),
    "autoscaling_groups": ("autoscaling_group",),
    "ecs_services": ("ecs_service",),
    "batch_environments": ("batch_compute_environment",),
    "ecr_repositories": ("ecr_repository",),
    "eks_clusters": ("eks_cluster", "eks_nodegroup"),
    "s3_buckets": ("s3_bucket",),
    "efs_file_systems": ("efs_file_system",),
    "fsx_file_systems": ("fsx_file_system",),
    "backup_plans": ("backup_plan",),
}


def _database_types() -> None:
    from ccradar.inventory.aws_analytics import ANALYTICS_TYPES
    from ccradar.inventory.aws_databases import DATABASE_TYPES
    from ccradar.inventory.aws_platform import PLATFORM_TYPES

    COLLECTOR_TYPES.update(DATABASE_TYPES)
    COLLECTOR_TYPES.update(ANALYTICS_TYPES)
    COLLECTOR_TYPES.update(PLATFORM_TYPES)


_database_types()
