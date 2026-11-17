"""Carbon reporting (11.26)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from ccradar.costs import carbon
from ccradar.costs.ingest import CostLine, replace_billing_period
from ccradar.db import Database
from tests.helpers import make_org, sign_in_client


def line(
    usage: str,
    quantity: float,
    cost: float,
    region: str = "ap-south-1",
    service: str = "EC2",
    provider: str = "aws",
) -> CostLine:
    return CostLine(
        charge_date=date(2026, 11, 3),
        provider=provider,
        service_name=service,
        region=region,
        usage_type=usage,
        consumed_quantity=Decimal(str(quantity)),
        billed_cost=Decimal(str(cost)),
        effective_cost=Decimal(str(cost)),
        billing_currency="USD",
    )


@pytest.mark.parametrize(
    ("size", "n"), [("large", 2), ("4xlarge", 16), ("medium", 1), ("metal", None)]
)
def test_vcpus(size: str, n: int | None) -> None:
    assert carbon.vcpus(size) == n


def test_usage_based_where_possible_spend_otherwise(client: TestClient, db: Database) -> None:
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    lines = [
        line("APS3-BoxUsage:m5.xlarge", 100, 19.2),  # 100 h x 4 vCPU
        line("APS3-EBS:VolumeUsage.gp3", 500, 40, service="EBS"),  # 500 GB-month SSD
        line("APS3-DataTransfer-Out-Bytes", 50, 10, service="Data transfer"),  # spend-based
        line("Standard", 1, 100, region="westeurope", service="VMs", provider="azure"),
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
    r = sign_in_client(client, db, owner, org_id).get("/api/v1/carbon", params={"months": 1}).json()
    grid = 708.2 * 1.135 / 1000  # ap-south-1, with PUE
    compute = 100 * 4 * carbon.WATTS_PER_VCPU / 1000 * grid
    storage = 500 / 1000 * 1.2 * 730 / 1000 * grid
    spend = (10 + 100) * 0.18
    (month,) = r["months"]
    assert month["from_usage"] == round(compute + storage, 1)
    assert month["from_spend"] == round(spend, 1)
    assert r["total_kg"] == round(compute + storage + spend, 1)
    assert r["by_region"][0]["name"] in ("westeurope", "ap-south-1")
    assert 0 < r["measured_share"] < 100 and "embodied" in r["method"]
