"""Demo network lines (cross-zone, inter-region, EC2 egress) next to the main demo bill."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from ccradar.costs.ingest import CostLine, replace_billing_period
from ccradar.demo.seed import DemoContext, demo_seeder

LINES = [
    ("Amazon Elastic Compute Cloud", "APS3-DataTransfer-Regional-Bytes", 38.0),
    ("Amazon Elastic Compute Cloud", "APS3-USE1-AWS-Out-Bytes", 9.5),
    ("Amazon Elastic Compute Cloud", "APS3-DataTransfer-Out-Bytes", 22.0),
]


@demo_seeder("costs.network", order=21)
def seed_network(db: Session, ctx: DemoContext) -> None:
    months: dict = {}
    for d in range(1, 61):
        day = ctx.today - timedelta(days=d)
        wobble = 0.85 + ctx.rng.random() * 0.3
        for service, usage, daily in LINES:
            months.setdefault(day.replace(day=1), []).append(
                CostLine(
                    charge_date=day,
                    provider="aws",
                    service_name=service,
                    sub_account_id="222222222222",
                    region="ap-south-1",
                    usage_type=usage,
                    billed_cost=Decimal(str(round(daily * wobble, 2))),
                    effective_cost=Decimal(str(round(daily * wobble, 2))),
                    billing_currency="USD",
                    service_category="Networking",
                )
            )
    for month, lines in months.items():
        replace_billing_period(
            db,
            org_id=ctx.org_id,
            source_kind="demo",
            source_key="demo-network",
            billing_period=month,
            lines=lines,
        )
