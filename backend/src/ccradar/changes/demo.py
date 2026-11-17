"""Demo changes that explain the demo's cost movements."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

from sqlalchemy.orm import Session

from ccradar.changes.cloudtrail import SERVICES
from ccradar.changes.service import ChangeIn, record
from ccradar.db.base import utcnow
from ccradar.demo.seed import DemoContext, demo_seeder


@demo_seeder("changes.events", order=25)
def seed_changes(db: Session, ctx: DemoContext) -> None:
    def at(days_ago: int, hour: int) -> datetime:
        return datetime.combine(ctx.today - timedelta(days=days_ago), time(hour), UTC)

    events = [
        (
            ChangeIn(
                occurred_at=at(21, 10),
                kind="deployment",
                title="acme/checkout deployed v2.3.0 to production",
                description="Moved checkout web tier to larger instances for the sale.",
                service="Amazon Elastic Compute Cloud",
                account="222222222222",
                environment="production",
                actor="ravi",
                external_id="deployment:demo-1",
                url="https://github.com/acme/checkout/releases/tag/v2.3.0",
            ),
            "github",
        ),
        (
            ChangeIn(
                occurred_at=at(6, 14),
                kind="infrastructure",
                title="ResizeCluster analytics-warehouse (ra3.4xlarge x4 -> x10)",
                service="Amazon Redshift",
                account="444444444444",
                region="ap-south-1",
                resource_id="analytics-warehouse",
                actor="arn:aws:iam::444444444444:user/data-eng",
                external_id="demo-ct-1",
            ),
            "cloudtrail",
        ),
        (
            ChangeIn(
                occurred_at=at(35, 9),
                kind="deployment",
                title="acme/search deployed v5.1.0 to production",
                service="Amazon DynamoDB",
                account="222222222222",
                environment="production",
                actor="meera",
                external_id="deployment:demo-2",
            ),
            "github",
        ),
        (
            ChangeIn(
                occurred_at=at(12, 16),
                kind="config",
                title="Enabled Bedrock assistant for all users",
                service="Amazon Bedrock",
                environment="production",
                actor="product",
                external_id="demo-api-1",
            ),
            "api",
        ),
    ]
    for change, source in events:
        record(db, ctx.org_id, change, source=source)
    # Live Radar (10.1): changes of the last few hours, priced before they reach the bill.
    now = utcnow()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    live = [
        (2, "RunInstances", "4 x m5.4xlarge", 2243.04, "ravi", "ap-south-1", "222222222222", "ec2"),
        (
            5,
            "CreateDBInstance",
            "db.r5.2xlarge multi-AZ",
            2970.33,
            "data-eng",
            "ap-south-1",
            "444444444444",
            "rds",
        ),
        (
            7,
            "CreateNatGateway",
            "NAT gateway (data processed is extra)",
            32.85,
            "meera",
            "us-east-1",
            "222222222222",
            "ec2",
        ),
        (
            9,
            "TerminateInstances",
            "terminated 6 x c5.2xlarge",
            -1489.2,
            "platform-bot",
            "us-east-1",
            "333333333333",
            "ec2",
        ),
        (20, "CreateVolume", "2000 GB gp3", 160.0, "ravi", "ap-south-1", "222222222222", "ec2"),
    ]
    for n, (hours_ago, name, label, monthly, actor, region, account, svc) in enumerate(live):
        change = ChangeIn(
            # The newest three stay within today, so the demo always shows changes that
            # are not in the bill yet, even just after midnight.
            occurred_at=max(now - timedelta(hours=hours_ago), midnight)
            if n < 3
            else now - timedelta(hours=hours_ago),
            kind="infrastructure",
            title=name,
            service=SERVICES.get(f"{svc}.amazonaws.com"),
            account=account,
            region=region,
            actor=f"arn:aws:sts::{account}:assumed-role/Engineer/{actor}",
            external_id=f"demo-radar-{n}",
            attributes={
                "event_source": f"{svc}.amazonaws.com",
                "event_name": name,
                "impact": {
                    "monthly": monthly,
                    "currency": "USD",
                    "source": "bill" if n % 2 == 0 else "list price",
                    "label": label,
                },
            },
        )
        record(db, ctx.org_id, change, source="cloudtrail")
