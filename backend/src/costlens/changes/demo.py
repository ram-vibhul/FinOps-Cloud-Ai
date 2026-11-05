"""Demo changes that explain the demo's cost movements."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

from sqlalchemy.orm import Session

from costlens.changes.service import ChangeIn, record
from costlens.demo.seed import DemoContext, demo_seeder


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
