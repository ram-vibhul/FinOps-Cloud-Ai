from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ccradar.accounts.roles import Role
from ccradar.costs.ingest import CostLine, replace_billing_period
from ccradar.costs.network import classify
from ccradar.db import Database
from ccradar.db.tenant import set_tenant
from ccradar.findings.service import run
from ccradar.inventory.aws import Found
from ccradar.inventory.service import upsert
from ccradar.models import Finding
from tests.helpers import add_to_org, make_org, sign_in_client


@pytest.mark.parametrize(
    ("usage", "service", "kind"),
    [
        ("USE1-NatGateway-Bytes", "Amazon Virtual Private Cloud", "nat_processing"),
        ("APS3-DataTransfer-Regional-Bytes", "Amazon Elastic Compute Cloud", "cross_az"),
        ("USE1-USW2-AWS-Out-Bytes", "Amazon Elastic Compute Cloud", "inter_region"),
        ("DataTransfer-Out-Bytes", "Amazon Elastic Compute Cloud", "internet_out"),
        ("US-DataTransfer-Out-Bytes", "Amazon CloudFront", "cdn_out"),
        ("USE1-TransitGateway-Bytes", "Amazon VPC", "transit_gateway"),
        ("USE1-VpcEndpoint-Bytes", "Amazon VPC", "vpc_endpoint"),
        ("DataProcessing-Bytes", "Amazon CloudWatch", None),  # log ingestion, not network
        ("BoxUsage:m6i.large", "Amazon Elastic Compute Cloud", None),
        (None, None, None),
    ],
)
def test_classify(usage, service, kind) -> None:
    assert classify(usage, service) == kind


def line(
    d: int,
    usage: str,
    cost: float,
    resource: str | None = None,
    service: str = "Amazon Elastic Compute Cloud",
    region: str = "us-east-1",
) -> CostLine:
    return CostLine(
        charge_date=date(2026, 11, d),
        provider="aws",
        service_name=service,
        sub_account_id="111",
        region=region,
        resource_id=resource,
        usage_type=usage,
        billed_cost=Decimal(str(cost)),
        effective_cost=Decimal(str(cost)),
        billing_currency="USD",
    )


def test_api_and_detectors(client: TestClient, db: Database) -> None:
    org_id, owner = make_org(db, "Acme", "owner@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        upsert(
            s,
            org_id,
            "111",
            [
                Found(
                    "Amazon Virtual Private Cloud",
                    "nat_gateway",
                    "nat-1",
                    "us-east-1",
                    state="available",
                    attributes={"vpc": "vpc-123"},
                )
            ],
        )
    lines = []
    for d in range(1, 31):
        lines += [
            line(d, "USE1-NatGateway-Bytes", 3.0, "nat-1", "Amazon Virtual Private Cloud"),
            line(d, "USE1-DataTransfer-Regional-Bytes", 2.0),
            line(d, "DataTransfer-Out-Bytes", 0.5),  # 15/month: below the review threshold
            line(d, "USE1-USW2-AWS-Out-Bytes", 1.5),
            line(d, "BoxUsage:m6i.large", 10.0, "i-1"),
        ]
    with db.session() as s:
        replace_billing_period(
            s,
            org_id=org_id,
            source_kind="t",
            source_key="t",
            billing_period=date(2026, 11, 1),
            lines=lines,
        )
    c = sign_in_client(client, db, owner, org_id)
    r = c.get("/api/v1/costs/network", params={"days": 30}).json()
    assert r["total"] == pytest.approx(30 * 7.0)
    cats = {x["key"]: x for x in r["categories"]}
    assert cats["nat_processing"]["cost"] == 90.0 and cats["cross_az"]["cost"] == 60.0
    assert r["daily"]["series"][0]["key"] == "nat_processing" and len(r["daily"]["periods"]) == 30
    assert r["top"][0]["resource_id"] == "nat-1"
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        run(
            s,
            org_id,
            only=[
                "aws.network_nat",
                "aws.network_cross_az",
                "aws.network_egress",
                "aws.network_inter_region",
            ],
        )
        rows = {f.detector: f for f in s.scalars(select(Finding))}
    nat = rows["aws.network_nat"]
    assert "nat-1 processes 91 USD/month" in nat.title and float(nat.savings_monthly) == 0
    assert "--vpc-id vpc-123" in nat.commands[0] and "com.amazonaws.us-east-1.s3" in nat.commands[0]
    assert (
        rows["aws.network_cross_az"].title
        == "Traffic between zones costs 61 USD/month in 111 / us-east-1"
    )
    assert (
        "aws.network_inter_region" in rows and "aws.network_egress" not in rows
    )  # 15/month: too small
    other_id, other = make_org(db, "Other", "o@other.com")
    assert (
        sign_in_client(client, db, other, other_id).get("/api/v1/costs/network").json()["total"]
        == 0
    )


def test_demo_network(client: TestClient, db: Database) -> None:
    from ccradar.demo.seed import ensure_demo, load_all_seeders

    load_all_seeders()
    with db.session() as s:
        demo_id = ensure_demo(s).id
    _, visitor = make_org(db, "Visitor", "visitor@x.com")
    add_to_org(db, demo_id, "visitor@x.com", Role.VIEWER)
    v = sign_in_client(client, db, visitor, demo_id)
    kinds = {c["key"] for c in v.get("/api/v1/costs/network").json()["categories"]}
    assert {"nat_processing", "cross_az", "inter_region", "internet_out", "cdn_out"} <= kinds
    detectors = {i["detector"] for i in v.get("/api/v1/findings").json()["items"]}
    assert {"aws.network_nat", "aws.network_cross_az"} <= detectors
