"""Demo inventory matching the demo's costs (and some waste for later checks)."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

from sqlalchemy.orm import Session

from ccradar.demo.seed import DemoContext, demo_seeder
from ccradar.inventory.aws import Found
from ccradar.inventory.service import upsert

PROD = "222222222222"


@demo_seeder("inventory.aws", order=22)
def seed_inventory(db: Session, ctx: DemoContext) -> None:
    def ago(days: int) -> datetime:
        return datetime.combine(ctx.today - timedelta(days=days), time(9), UTC)

    team = {
        "team": "payments",
        "project": "checkout",
        "env": "production",
        "owner": "ravi@acme.example",
    }
    items = [
        Found(
            "Amazon Elastic Compute Cloud",
            "ec2_instance",
            f"i-0{(int(PROD) * 7919 + i * 104729) % 16**15:015x}",
            "ap-south-1",
            name=f"checkout-web-{i + 1}",
            state="running",
            created_at=ago(200 - i * 10),
            tags=team,
            attributes={
                "instance_type": "m6i.xlarge",
                "lifecycle": "on-demand",
                "architecture": "x86_64",
            },
        )
        for i in range(6)
    ]
    items += [
        Found(
            "Amazon Elastic Compute Cloud",
            "ec2_instance",
            "i-0sandbox00000001",
            "us-west-2",
            name="old-experiment",
            state="stopped",
            created_at=ago(410),
            tags={},
            attributes={"instance_type": "t3.large"},
        ),
        Found(
            "Amazon Elastic Compute Cloud",
            "ebs_volume",
            "vol-0unattached0001",
            "ap-south-1",
            name="checkout-db-backup",
            state="available",
            created_at=ago(160),
            tags={"team": "payments"},
            attributes={"size_gb": 500, "volume_type": "gp2", "attached_to": []},
        ),
        Found(
            "Amazon Elastic Compute Cloud",
            "ebs_volume",
            "vol-0inuse00000001",
            "ap-south-1",
            name="checkout-web-1-root",
            state="in-use",
            created_at=ago(200),
            tags=team,
            attributes={"size_gb": 100, "volume_type": "gp3", "attached_to": ["i-0x"]},
        ),
        Found(
            "Amazon Virtual Private Cloud",
            "elastic_ip",
            "eipalloc-0idle0000001",
            "ap-south-1",
            name="13.200.1.10",
            state="unattached",
            tags={},
            attributes={"public_ip": "13.200.1.10"},
        ),
        Found(
            "Amazon Virtual Private Cloud",
            "nat_gateway",
            "nat-0shared00000001",
            "ap-south-1",
            name="shared-nat",
            state="available",
            created_at=ago(500),
            tags={"team": "platform"},
        ),
        Found(
            "Elastic Load Balancing",
            "load_balancer",
            "arn:aws:elasticloadbalancing:ap-south-1:222222222222:loadbalancer/app/legacy/1",
            "ap-south-1",
            name="legacy-admin",
            state="active",
            created_at=ago(700),
            attributes={"type": "application"},
        ),
        Found(
            "Amazon Relational Database Service",
            "rds_instance",
            "arn:aws:rds:ap-south-1:222222222222:db:checkout-db",
            "ap-south-1",
            name="checkout-db",
            state="available",
            created_at=ago(900),
            tags=team,
            attributes={
                "instance_class": "db.r6g.2xlarge",
                "engine": "postgres",
                "engine_version": "11.22",
                "multi_az": True,
                "storage_gb": 1000,
            },
        ),
        Found(
            "Amazon Elastic Compute Cloud",
            "ebs_snapshot",
            "snap-0old0000000001",
            "ap-south-1",
            name="pre-migration-2023",
            state="completed",
            created_at=ago(780),
            tags={},
            attributes={"size_gb": 800},
        ),
    ]
    items += _compute(ago)
    seen = datetime.combine(ctx.today, time(6), UTC)
    upsert(db, ctx.org_id, PROD, items + _storage(ago), seen_at=seen)
    upsert(db, ctx.org_id, "444444444444", [_data_lake(ago)], seen_at=seen)


def _data_lake(ago) -> Found:
    return Found(
        "Amazon Simple Storage Service",
        "s3_bucket",
        "acme-data-lake",
        "ap-south-1",
        name="acme-data-lake",
        state="active",
        created_at=ago(1100),
        tags={"team": "data", "project": "lake"},
        attributes={
            "versioning": "Enabled",
            "lifecycle_rules": 0,
            "transitions": [],
            "expires_noncurrent": False,
            "aborts_multipart": False,
            "expires_objects": False,
            "intelligent_tiering_configs": 0,
        },
    )


def _storage(ago) -> list[Found]:
    region, acct = "ap-south-1", PROD
    return [
        Found(
            "Amazon Elastic File System",
            "efs_file_system",
            f"arn:aws:elasticfilesystem:{region}:{acct}:file-system/fs-0shared",
            region,
            name="shared-home",
            state="available",
            created_at=ago(600),
            tags={"team": "platform"},
            attributes={
                "file_system_id": "fs-0shared",
                "bytes_total": 800e9,
                "bytes_standard": 800e9,
                "throughput_mode": "elastic",
                "transition_to_ia": None,
            },
        ),
        Found(
            "Amazon Elastic Compute Cloud",
            "ebs_volume",
            "vol-0io2database01",
            region,
            name="ledger-db-data",
            state="in-use",
            created_at=ago(300),
            tags={"team": "payments"},
            attributes={
                "size_gb": 500,
                "volume_type": "io2",
                "iops": 20000,
                "attached_to": ["i-0x"],
            },
        ),
        Found(
            "AWS Backup",
            "backup_plan",
            f"arn:aws:backup:{region}:{acct}:backup-plan:daily",
            region,
            name="daily-everything",
            state="active",
            created_at=ago(700),
            attributes={
                "rules": [
                    {"name": "daily", "delete_after_days": 35, "cold_after_days": None},
                    {"name": "monthly", "delete_after_days": None, "cold_after_days": None},
                ]
            },
        ),
    ]


def _compute(ago) -> list[Found]:
    """Compute and container resources for the AWS audit's compute checks."""
    region, acct = "ap-south-1", PROD
    return [
        Found(
            "AWS Lambda",
            "lambda_function",
            f"arn:aws:lambda:{region}:{acct}:function:payment-webhooks",
            region,
            name="payment-webhooks",
            state="Active",
            tags={"team": "payments"},
            attributes={
                "memory_mb": 1024,
                "runtime": "python3.12",
                "architectures": ["x86_64"],
                "package_type": "Zip",
            },
        ),
        Found(
            "Amazon Elastic Kubernetes Service",
            "eks_cluster",
            f"arn:aws:eks:{region}:{acct}:cluster/shared",
            region,
            name="shared",
            state="active",
            created_at=ago(800),
            tags={"team": "platform"},
            attributes={"version": "1.29", "support_type": "EXTENDED"},
        ),
        Found(
            "Amazon Elastic Kubernetes Service",
            "eks_nodegroup",
            f"arn:aws:eks:{region}:{acct}:nodegroup/shared/general/1",
            region,
            name="general",
            state="active",
            attributes={
                "cluster": "shared",
                "capacity_type": "ON_DEMAND",
                "instance_types": ["m6i.xlarge"],
                "min": 3,
                "max": 10,
                "desired": 4,
            },
        ),
        Found(
            "Amazon Elastic Container Service",
            "ecs_service",
            f"arn:aws:ecs:{region}:{acct}:service/staging/search-api",
            region,
            name="search-api",
            state="active",
            tags={"env": "staging", "team": "search"},
            attributes={
                "cluster": "staging",
                "launch_type": "FARGATE",
                "capacity_providers": [],
                "desired": 2,
                "running": 2,
            },
        ),
        Found(
            "AWS Batch",
            "batch_compute_environment",
            f"arn:aws:batch:{region}:{acct}:compute-environment/nightly-reports",
            region,
            name="nightly-reports",
            state="enabled",
            attributes={
                "managed": True,
                "compute_type": "EC2",
                "min_vcpus": 16,
                "max_vcpus": 256,
            },
        ),
        Found(
            "Amazon EC2 Container Registry (ECR)",
            "ecr_repository",
            f"arn:aws:ecr:{region}:{acct}:repository/checkout-web",
            region,
            name="checkout-web",
            state="active",
            created_at=ago(900),
            attributes={
                "lifecycle_policy": False,
                "image_count": 2140,
                "total_bytes": 620 * 2**30,
                "untagged_bytes": 310 * 2**30,
                "stale_bytes": 240 * 2**30,
            },
        ),
    ]


@demo_seeder("inventory.metrics", order=23)
def seed_metrics(db: Session, ctx: DemoContext) -> None:
    """14 days of CloudWatch-style usage: checkout-web-1 is over-sized, the rest are busy."""
    from sqlalchemy import select

    from ccradar.models import CloudResource, ResourceMetric

    peaks = {"checkout-web-1": 24.0}  # everything else peaks around 70%
    for r in db.scalars(
        select(CloudResource).where(
            CloudResource.org_id == ctx.org_id,
            CloudResource.state.in_(("running", "available")),
            CloudResource.resource_type.in_(("ec2_instance", "rds_instance")),
        )
    ):
        if r.resource_type == "ec2_instance":
            peak = peaks.get(r.name or "", 68.0 + ctx.rng.random() * 8)
            series = {"cpu_pct": (peak * 0.45, peak, peak * 0.1)}
        else:  # checkout-db: quiet, lots of free memory
            series = {
                "cpu_pct": (9.0, 18.0, 2.0),
                "connections": (35.0, 80.0, 12.0),
                "free_memory_bytes": (48 * 2**30, 52 * 2**30, 44 * 2**30),
            }
        for d in range(1, 15):
            day = ctx.today - timedelta(days=d)
            for metric, (avg, mx, mn) in series.items():
                wobble = 0.9 + ctx.rng.random() * 0.1
                db.add(
                    ResourceMetric(
                        org_id=ctx.org_id,
                        resource_id=r.resource_id,
                        metric=metric,
                        day=day,
                        avg=avg * wobble,
                        max=mx * wobble,
                        min=mn,
                        p95=mx * 0.9 * wobble,
                        samples=24,
                    )
                )
    db.flush()
