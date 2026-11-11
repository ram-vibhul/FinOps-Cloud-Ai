"""Demo organisation: 90 days of realistic multi-account AWS spend.

Five accounts (payer, production, staging, data platform, sandbox), the usual
services, weekly seasonality, steady growth, a step change in production EC2
three weeks ago (a "deployment"), a Redshift spike in the last six days (an
anomaly), a monthly Savings Plan fee and tax, and some untagged spend so the
data-quality views have something to show.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from ccradar.costs.ingest import CostLine, replace_billing_period
from ccradar.demo.seed import DemoContext, demo_seeder
from ccradar.models import CloudAccount

DAYS = 90
PAYER = "111111111111"
ACCOUNTS = {
    "222222222222": ("Production", "production"),
    "333333333333": ("Staging", "staging"),
    "444444444444": ("Data Platform", "production"),
    "555555555555": ("Sandbox", "development"),
}
# (account, service, category, region, usage type, base daily USD, team, project)
SERVICES = [
    (
        "222222222222",
        "Amazon Elastic Compute Cloud",
        "Compute",
        "ap-south-1",
        "BoxUsage:m6i.xlarge",
        1250,
        "payments",
        "checkout",
    ),
    (
        "222222222222",
        "Amazon Elastic Compute Cloud",
        "Compute",
        "us-east-1",
        "BoxUsage:c6g.2xlarge",
        560,
        "search",
        "search-api",
    ),
    (
        "222222222222",
        "Amazon Relational Database Service",
        "Databases",
        "ap-south-1",
        "InstanceUsage:db.r6g.2xlarge",
        650,
        "payments",
        "checkout",
    ),
    (
        "222222222222",
        "Amazon Elastic Kubernetes Service",
        "Compute",
        "ap-south-1",
        "AmazonEKS-Hours:perCluster",
        7.2,
        "platform",
        "shared-cluster",
    ),
    (
        "222222222222",
        "AWS Lambda",
        "Compute",
        "ap-south-1",
        "Lambda-GB-Second",
        42,
        "payments",
        "webhooks",
    ),
    (
        "222222222222",
        "Amazon CloudFront",
        "Networking",
        "global",
        "DataTransfer-Out-Bytes",
        88,
        "platform",
        "web",
    ),
    (
        "222222222222",
        "Amazon DynamoDB",
        "Databases",
        "ap-south-1",
        "ReadCapacityUnit-Hrs",
        110,
        "search",
        "search-api",
    ),
    (
        "222222222222",
        "Amazon CloudWatch",
        "Management and Governance",
        "ap-south-1",
        "DataProcessing-Bytes",
        70,
        None,
        None,
    ),
    (
        "222222222222",
        "Elastic Load Balancing",
        "Networking",
        "ap-south-1",
        "LoadBalancerUsage",
        58,
        "platform",
        "web",
    ),
    (
        "222222222222",
        "Amazon Virtual Private Cloud",
        "Networking",
        "ap-south-1",
        "NatGateway-Bytes",
        95,
        "platform",
        "shared-cluster",
    ),
    (
        "222222222222",
        "Amazon Bedrock",
        "AI and Machine Learning",
        "us-east-1",
        "Claude-InputTokens",
        35,
        "search",
        "assistant",
    ),
    (
        "333333333333",
        "Amazon Elastic Compute Cloud",
        "Compute",
        "ap-south-1",
        "BoxUsage:m6i.large",
        380,
        "payments",
        "checkout",
    ),
    (
        "333333333333",
        "Amazon Relational Database Service",
        "Databases",
        "ap-south-1",
        "InstanceUsage:db.r6g.large",
        150,
        "payments",
        "checkout",
    ),
    (
        "333333333333",
        "Amazon CloudWatch",
        "Management and Governance",
        "ap-south-1",
        "DataProcessing-Bytes",
        24,
        None,
        None,
    ),
    (
        "444444444444",
        "Amazon Redshift",
        "Analytics",
        "ap-south-1",
        "Node:ra3.4xlarge",
        420,
        "data",
        "warehouse",
    ),
    (
        "444444444444",
        "Amazon Simple Storage Service",
        "Storage",
        "ap-south-1",
        "TimedStorage-ByteHrs",
        310,
        "data",
        "lake",
    ),
    (
        "444444444444",
        "AWS Glue",
        "Analytics",
        "ap-south-1",
        "ETL-DPU-Hour",
        82,
        "data",
        "pipelines",
    ),
    (
        "444444444444",
        "Amazon Elastic Compute Cloud",
        "Compute",
        "ap-south-1",
        "BoxUsage:r5.4xlarge",
        610,
        "data",
        "spark",
    ),
    (
        "555555555555",
        "Amazon Elastic Compute Cloud",
        "Compute",
        "us-west-2",
        "BoxUsage:t3.large",
        75,
        None,
        None,
    ),
    (
        "555555555555",
        "Amazon Simple Storage Service",
        "Storage",
        "us-west-2",
        "TimedStorage-ByteHrs",
        12,
        None,
        None,
    ),
]
EC2_PROD_INSTANCES = 6


# Bill lines that carry a resource id, so the inventory's checks can price them.
RESOURCE_IDS = {
    (
        "222222222222",
        "AWS Lambda",
    ): "arn:aws:lambda:ap-south-1:222222222222:function:payment-webhooks",
    ("222222222222", "Amazon Elastic Kubernetes Service"): (
        "arn:aws:eks:ap-south-1:222222222222:cluster/shared"
    ),
    ("444444444444", "Amazon Simple Storage Service"): "acme-data-lake",
}


def _money(value: float) -> Decimal:
    return Decimal(str(round(value, 4)))


def _lines(ctx: DemoContext, month: date) -> Iterator[CostLine]:
    rng = ctx.rng
    first_day = ctx.today - timedelta(days=DAYS - 1)
    for offset in range(DAYS):
        day = first_day + timedelta(days=offset)
        if day.replace(day=1) != month:
            continue
        growth = 1 + 0.0012 * offset
        weekend = day.weekday() >= 5
        for account, service, category, region, usage, base, team, project in SERVICES:
            env = ACCOUNTS[account][1]
            factor = growth * (1 + rng.uniform(-0.05, 0.05))
            if weekend and env != "production":
                factor *= 0.82
            if (
                account == "222222222222"
                and service.endswith("Compute Cloud")
                and offset >= DAYS - 21
            ):
                factor *= 1.12  # step change after a deployment three weeks ago
            if service == "Amazon Redshift" and offset >= DAYS - 6:
                factor *= 2.6  # anomaly: someone left a big cluster resized
            if service == "Amazon Bedrock":
                factor *= 1 + offset / 45  # AI spend growing fast
            cost = base * factor
            tags = {"team": team, "project": project, "env": env} if team else {}
            if team and rng.random() < 0.08:
                tags.pop("project")  # imperfect tagging, as in real life
            if (
                account == "222222222222"
                and region == "ap-south-1"
                and service.endswith("Compute Cloud")
            ):
                weights = [math.exp(rng.uniform(-0.3, 0.3)) for _ in range(EC2_PROD_INSTANCES)]
                total_w = sum(weights)
                for i, w in enumerate(weights):
                    part = cost * w / total_w
                    yield CostLine(
                        charge_date=day,
                        provider="aws",
                        billing_account_id=PAYER,
                        sub_account_id=account,
                        sub_account_name=ACCOUNTS[account][0],
                        service_name=service,
                        service_category=category,
                        region=region,
                        availability_zone=f"{region}{'abc'[i % 3]}",
                        resource_id=f"i-0{(int(account) * 7919 + i * 104729) % 16**15:015x}",
                        resource_name=f"checkout-web-{i + 1}",
                        resource_type="EC2 instance",
                        usage_type=usage,
                        operation="RunInstances",
                        billed_cost=_money(part),
                        effective_cost=_money(part * 0.78),
                        list_cost=_money(part),
                        billing_currency="USD",
                        pricing_category="Committed",
                        commitment_discount_type="Savings Plan",
                        consumed_quantity=_money(24),
                        consumed_unit="Hours",
                        tags=tags,
                    )
                continue
            yield CostLine(
                charge_date=day,
                provider="aws",
                billing_account_id=PAYER,
                sub_account_id=account,
                sub_account_name=ACCOUNTS[account][0],
                service_name=service,
                service_category=category,
                region=None if region == "global" else region,
                resource_id=RESOURCE_IDS.get((account, service)),
                usage_type=usage,
                billed_cost=_money(cost),
                effective_cost=_money(cost),
                list_cost=_money(cost),
                billing_currency="USD",
                tags=tags,
            )
        if day.day == 1:
            yield CostLine(
                charge_date=day,
                provider="aws",
                billing_account_id=PAYER,
                sub_account_id=PAYER,
                sub_account_name="Demo Payer",
                service_name="Savings Plans for AWS Compute usage",
                service_category="Compute",
                charge_category="Purchase",
                pricing_category="Committed",
                billed_cost=_money(9000),
                effective_cost=_money(0),
                billing_currency="USD",
            )
            yield CostLine(
                charge_date=day,
                provider="aws",
                billing_account_id=PAYER,
                sub_account_id=PAYER,
                sub_account_name="Demo Payer",
                service_name="Tax",
                charge_category="Tax",
                pricing_category=None,
                billed_cost=_money(2100),
                effective_cost=_money(2100),
                billing_currency="USD",
            )


@demo_seeder("costs.aws", order=20)
def seed_costs(db: Session, ctx: DemoContext) -> None:
    payer = CloudAccount(
        org_id=ctx.org_id,
        provider="aws",
        external_id=PAYER,
        name="Demo Payer",
        is_billing_account=True,
    )
    db.add(payer)
    db.flush()
    for external_id, (name, env) in ACCOUNTS.items():
        db.add(
            CloudAccount(
                org_id=ctx.org_id,
                provider="aws",
                external_id=external_id,
                name=name,
                environment=env,
                parent_id=payer.id,
            )
        )
    db.flush()
    first_day = ctx.today - timedelta(days=DAYS - 1)
    month = first_day.replace(day=1)
    while month <= ctx.today:
        replace_billing_period(
            db,
            org_id=ctx.org_id,
            source_kind="demo",
            source_key="demo:aws",
            billing_period=month,
            lines=_lines(ctx, month),
        )
        month = (month + timedelta(days=32)).replace(day=1)
