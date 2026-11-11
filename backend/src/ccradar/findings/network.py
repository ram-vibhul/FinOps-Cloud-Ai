"""Network reviews: where data transfer costs money and what usually helps. No saving is
claimed: which traffic can move needs flow logs (4.21)."""

from __future__ import annotations

import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from ccradar.costs.network import CATEGORIES, network_costs
from ccradar.findings.registry import FindingIn, detector
from ccradar.models import CloudResource

MIN_MONTHLY = 30.0
MONTH = 30.4


def _monthly(db: Session, org_id: uuid.UUID, currency: str) -> list[dict]:
    data = network_costs(db, org_id, days=30, currency=currency)
    return [{**t, "monthly": t["cost"] * MONTH / 30} for t in data["top"]] if data["top"] else []


def _by_place(rows: list[dict], category: str) -> dict[tuple[str | None, str | None], float]:
    out: dict[tuple[str | None, str | None], float] = defaultdict(float)
    for r in rows:
        if r["category"] == category:
            out[(r["account"], r["region"])] += r["monthly"]
    return out


def _money(v: float, currency: str) -> str:
    return f"{v:,.0f} {currency}"


@detector("aws.network_nat", category="network", title="NAT gateway data processing")
def network_nat(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    rows = [r for r in _monthly(db, org_id, currency) if r["category"] == "nat_processing"]
    vpcs = {
        r.resource_id: (r.attributes or {}).get("vpc")
        for r in db.scalars(
            select(CloudResource).where(CloudResource.resource_type == "nat_gateway")
        )
    }
    grouped: dict[tuple, float] = defaultdict(float)
    for r in rows:
        grouped[
            (r["resource_id"] or f"{r['account']}/{r['region']}", r["account"], r["region"])
        ] += r["monthly"]
    out = []
    for (key, account, region), monthly in grouped.items():
        if monthly < MIN_MONTHLY:
            continue
        vpc = vpcs.get(key) or "<vpc-id>"
        out.append(
            FindingIn(
                key=key,
                title=f"NAT gateway {key} processes {_money(monthly, currency)}/month of traffic",
                description=(
                    "Every byte through a NAT gateway is charged. Traffic to S3 and DynamoDB can use free "
                    "gateway endpoints instead, and frequent AWS API traffic (ECR, STS, CloudWatch) can "
                    "use interface endpoints. Flow logs show exactly which destinations dominate."
                ),
                savings_monthly=0.0,
                currency=currency,
                risk="low",
                effort="low",
                overlap=f"net:{key}",
                provider="aws",
                account_id=account,
                region=region,
                service="Amazon Virtual Private Cloud",
                resource_type="nat_gateway",
                resource_id=key,
                steps=[
                    "Add S3 and DynamoDB gateway endpoints to the VPC's private route tables (free).",
                    "Add interface endpoints for ECR, STS and CloudWatch if container pulls or SDK calls are heavy.",
                    "Re-check this cost after a week.",
                ],
                commands=[
                    f"aws ec2 create-vpc-endpoint --region {region} --vpc-id {vpc} --vpc-endpoint-type Gateway "
                    f"--service-name com.amazonaws.{region}.{svc} --route-table-ids <private-route-table-ids>"
                    for svc in ("s3", "dynamodb")
                ]
                if region
                else [],
                evidence={
                    "monthly_cost": round(monthly, 2),
                    "category": CATEGORIES["nat_processing"],
                },
            )
        )
    return out


def _place_review(
    db: Session,
    org_id: uuid.UUID,
    currency: str,
    category: str,
    title: str,
    description: str,
    steps: list[str],
) -> list[FindingIn]:
    out = []
    for (account, region), monthly in _by_place(_monthly(db, org_id, currency), category).items():
        if monthly < MIN_MONTHLY:
            continue
        where = " / ".join(x for x in (account, region) if x) or "all accounts"
        out.append(
            FindingIn(
                key=f"{account}:{region}",
                title=title.format(cost=_money(monthly, currency), where=where),
                description=description,
                savings_monthly=0.0,
                currency=currency,
                risk="low",
                effort="medium",
                overlap=f"net:{category}:{account}:{region}",
                provider="aws",
                account_id=account,
                region=region,
                service="Data transfer",
                resource_type=category,
                steps=steps,
                evidence={"monthly_cost": round(monthly, 2), "category": CATEGORIES[category]},
            )
        )
    return out


@detector("aws.network_cross_az", category="network", title="Traffic between availability zones")
def network_cross_az(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    return _place_review(
        db,
        org_id,
        currency,
        "cross_az",
        "Traffic between zones costs {cost}/month in {where}",
        "Data between availability zones is charged in both directions. Chatty services, databases and "
        "message brokers spread over zones usually cause most of it.",
        [
            "In Kubernetes, turn on topology-aware routing (annotation service.kubernetes.io/topology-mode: Auto).",
            "Point clients at same-zone replicas (read replicas, Kafka rack awareness, cache nodes).",
            "Check load balancers: cross-zone load balancing on NLBs adds zone traffic.",
        ],
    )


@detector("aws.network_egress", category="network", title="Data out to the internet")
def network_egress(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    return _place_review(
        db,
        org_id,
        currency,
        "internet_out",
        "Data out to the internet costs {cost}/month in {where}",
        "Serving downloads, images or API responses straight from EC2 or load balancers is the most "
        "expensive way out; CloudFront is cheaper per GB and caches repeated responses.",
        [
            "Put CloudFront in front of public load balancers and S3 buckets.",
            "Turn on compression (gzip/brotli) for API and web responses.",
            "Find the biggest senders in the Network costs page (top resources).",
        ],
    )


@detector("aws.network_inter_region", category="network", title="Traffic between regions")
def network_inter_region(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    return _place_review(
        db,
        org_id,
        currency,
        "inter_region",
        "Traffic between regions costs {cost}/month from {where}",
        "Calls and replication between regions are charged per GB on the sending side.",
        [
            "Keep services that talk a lot in the same region.",
            "Replicate read-heavy data to where it is read instead of fetching it each time.",
            "Check backups and log shipping that copy data across regions.",
        ],
    )
