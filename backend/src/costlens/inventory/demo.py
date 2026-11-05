"""Demo inventory matching the demo's costs (and some waste for later checks)."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

from sqlalchemy.orm import Session

from costlens.demo.seed import DemoContext, demo_seeder
from costlens.inventory.aws import Found
from costlens.inventory.service import upsert

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
    upsert(db, ctx.org_id, PROD, items, seen_at=datetime.combine(ctx.today, time(6), UTC))
