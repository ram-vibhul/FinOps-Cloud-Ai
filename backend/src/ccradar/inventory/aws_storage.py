"""Storage inventory: S3 buckets, EFS and FSx file systems, AWS Backup plans
(read-only configuration calls; never object data)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ccradar.inventory.aws import Found, _pages, _tags
from ccradar.inventory.aws_compute import _code

MAX_BUCKETS = 1000
MAX_CHILDREN = 200


def _try(call: Any, missing: str, **kw: Any) -> tuple[dict[str, Any] | None, bool]:
    """(answer, known): ``missing`` errors mean "not configured"; others mean unknown."""
    try:
        return call(**kw), True
    except Exception as error:
        return None, _code(error) == missing


def _lifecycle(rules: list[dict[str, Any]]) -> dict[str, Any]:
    on = [r for r in rules if r.get("Status") == "Enabled"]
    classes = sorted(
        {
            t.get("StorageClass")
            for r in on
            for t in (r.get("Transitions") or []) + (r.get("NoncurrentVersionTransitions") or [])
            if t.get("StorageClass")
        }
    )
    return {
        "lifecycle_rules": len(on),
        "transitions": classes,
        "expires_noncurrent": any(r.get("NoncurrentVersionExpiration") for r in on),
        "aborts_multipart": any(r.get("AbortIncompleteMultipartUpload") for r in on),
        "expires_objects": any(r.get("Expiration") for r in on),
    }


def s3_buckets(client: Any, region: str) -> Iterator[Found]:
    seen = 0
    for page in _pages(
        client.list_buckets,
        "ContinuationToken",
        "ContinuationToken",
        BucketRegion=region,
        MaxBuckets=1000,
    ):
        for b in page.get("Buckets", []):
            if b.get("BucketRegion", region) != region or seen >= MAX_BUCKETS:
                continue
            seen += 1
            name = b["Name"]
            attrs: dict[str, Any] = {}
            ver, ok = _try(client.get_bucket_versioning, "", Bucket=name)
            attrs["versioning"] = (ver or {}).get("Status", "Disabled") if ok else None
            life, known = _try(
                client.get_bucket_lifecycle_configuration,
                "NoSuchLifecycleConfiguration",
                Bucket=name,
            )
            if life is not None:
                attrs.update(_lifecycle(life.get("Rules") or []))
            elif known:
                attrs.update(_lifecycle([]))
            else:
                attrs["lifecycle_rules"] = None  # could not read
            tiering, ok = _try(
                client.list_bucket_intelligent_tiering_configurations, "", Bucket=name
            )
            attrs["intelligent_tiering_configs"] = (
                len((tiering or {}).get("IntelligentTieringConfigurationList") or [])
                if ok
                else None
            )
            tagging, _ = _try(client.get_bucket_tagging, "NoSuchTagSet", Bucket=name)
            yield Found(
                "Amazon Simple Storage Service",
                "s3_bucket",
                name,  # the bill uses the bucket name as the resource id
                region,
                name=name,
                state="active",
                created_at=b.get("CreationDate"),
                tags=_tags((tagging or {}).get("TagSet")),
                attributes=attrs,
            )


def efs_file_systems(client: Any, region: str) -> Iterator[Found]:
    for page in _pages(client.describe_file_systems, "Marker", "NextMarker", MaxItems=100):
        for fs in page.get("FileSystems", [])[:MAX_CHILDREN]:
            size = fs.get("SizeInBytes") or {}
            life, _ = _try(
                client.describe_lifecycle_configuration, "", FileSystemId=fs["FileSystemId"]
            )
            policies = (life or {}).get("LifecyclePolicies") or []
            yield Found(
                "Amazon Elastic File System",
                "efs_file_system",
                fs.get("FileSystemArn") or fs["FileSystemId"],
                region,
                name=fs.get("Name") or fs["FileSystemId"],
                state=fs.get("LifeCycleState"),
                created_at=fs.get("CreationTime"),
                tags=_tags(fs.get("Tags")),
                attributes={
                    "file_system_id": fs["FileSystemId"],
                    "bytes_total": size.get("Value"),
                    "bytes_standard": size.get("ValueInStandard"),
                    "bytes_ia": size.get("ValueInIA"),
                    "bytes_archive": size.get("ValueInArchive"),
                    "throughput_mode": fs.get("ThroughputMode"),
                    "provisioned_mibps": fs.get("ProvisionedThroughputInMibps"),
                    "one_zone": bool(fs.get("AvailabilityZoneName")),
                    "transition_to_ia": next(
                        (p["TransitionToIA"] for p in policies if p.get("TransitionToIA")), None
                    )
                    if life is not None
                    else "unknown",
                },
            )


def fsx_file_systems(client: Any, region: str) -> Iterator[Found]:
    for page in _pages(client.describe_file_systems, "NextToken", "NextToken", MaxResults=100):
        for fs in page.get("FileSystems", [])[:MAX_CHILDREN]:
            kind = fs.get("FileSystemType")
            config = (
                fs.get("WindowsConfiguration")
                or fs.get("OntapConfiguration")
                or fs.get("OpenZFSConfiguration")
                or fs.get("LustreConfiguration")
                or {}
            )
            tags = _tags(fs.get("Tags"))
            yield Found(
                "Amazon FSx",
                "fsx_file_system",
                fs.get("ResourceARN") or fs["FileSystemId"],
                region,
                name=tags.get("Name") or fs["FileSystemId"],
                state=str(fs.get("Lifecycle") or "").lower() or None,
                created_at=fs.get("CreationTime"),
                tags=tags,
                attributes={
                    "type": kind,
                    "storage_gb": fs.get("StorageCapacity"),
                    "storage_type": fs.get("StorageType"),
                    "deployment": config.get("DeploymentType"),
                    "throughput_mbps": config.get("ThroughputCapacity")
                    or config.get("PerUnitStorageThroughput"),
                    "compression": config.get("DataCompressionType"),
                },
            )


def backup_plans(client: Any, region: str) -> Iterator[Found]:
    for page in _pages(client.list_backup_plans, "NextToken", "NextToken", MaxResults=100):
        for p in page.get("BackupPlansList", [])[:MAX_CHILDREN]:
            plan = client.get_backup_plan(BackupPlanId=p["BackupPlanId"]).get("BackupPlan") or {}
            rules = [
                {
                    "name": r.get("RuleName"),
                    "delete_after_days": (r.get("Lifecycle") or {}).get("DeleteAfterDays"),
                    "cold_after_days": (r.get("Lifecycle") or {}).get("MoveToColdStorageAfterDays"),
                    "schedule": r.get("ScheduleExpression"),
                }
                for r in plan.get("Rules") or []
            ]
            yield Found(
                "AWS Backup",
                "backup_plan",
                p.get("BackupPlanArn") or p["BackupPlanId"],
                region,
                name=p.get("BackupPlanName"),
                state="active",
                created_at=p.get("CreationDate"),
                attributes={"rules": rules},
            )


STORAGE_COLLECTORS = [
    ("s3", s3_buckets),
    ("efs", efs_file_systems),
    ("fsx", fsx_file_systems),
    ("backup", backup_plans),
]
