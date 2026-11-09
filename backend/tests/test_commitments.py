from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from costlens.accounts.roles import Role
from costlens.clouds.aws.commitment_job import snapshot
from costlens.clouds.aws.commitments import collect
from costlens.db import Database
from costlens.db.tenant import set_tenant
from costlens.findings.service import run
from costlens.models import CloudConnection, Finding
from tests.fakes.aws import FakeAws
from tests.helpers import add_to_org, make_org, sign_in_client

ROLE = "arn:aws:iam::222222222222:role/costlens"


class FakeCE:
    """Shapes as Cost Explorer returns them."""

    def __init__(self, deny_reservations: bool = False) -> None:
        self.calls: list[str] = []
        self.deny = deny_reservations

    def get_savings_plans_utilization(self, **kw):
        self.calls.append("sp_util")
        return {
            "Total": {
                "Utilization": {
                    "TotalCommitment": "1000",
                    "UsedCommitment": "820",
                    "UnusedCommitment": "180",
                    "UtilizationPercentage": "82",
                },
                "Savings": {"NetSavings": "300"},
            }
        }

    def get_savings_plans_coverage(self, **kw):
        self.calls.append("sp_cov")
        return {
            "SavingsPlansCoverages": [
                {"Coverage": {"SpendCoveredBySavingsPlans": "600", "OnDemandCost": "400"}}
            ]
        }

    def get_reservation_utilization(self, **kw):
        if self.deny:
            raise RuntimeError("AccessDenied")
        return {"Total": {"UtilizationPercentage": "100", "UnusedHours": "0", "NetRISavings": "50"}}

    def get_reservation_coverage(self, **kw):
        if self.deny:
            raise RuntimeError("AccessDenied")
        return {"Total": {"CoverageHours": {"CoverageHoursPercentage": "40"}}}

    def get_savings_plans_purchase_recommendation(self, **kw):
        amount = {"COMPUTE_SP": "120.5", "EC2_INSTANCE_SP": "150.0"}[kw["SavingsPlansType"]]
        return {
            "SavingsPlansPurchaseRecommendation": {
                "SavingsPlansPurchaseRecommendationSummary": {
                    "HourlyCommitmentToPurchase": "0.75",
                    "EstimatedMonthlySavingsAmount": amount,
                    "EstimatedSavingsPercentage": "22",
                    "CurrentOnDemandSpend": "700",
                    "CurrencyCode": "USD",
                }
            }
        }

    def get_reservation_purchase_recommendation(self, **kw):
        if kw["Service"] != "Amazon Relational Database Service":
            return {"Recommendations": []}
        return {
            "Recommendations": [
                {
                    "RecommendationDetails": [
                        {
                            "InstanceDetails": {
                                "RDSInstanceDetails": {
                                    "InstanceType": "db.r6g.large",
                                    "Region": "us-east-1",
                                    "DatabaseEngine": "PostgreSQL",
                                }
                            },
                            "RecommendedNumberOfInstancesToPurchase": "2",
                            "EstimatedMonthlySavingsAmount": "88.4",
                            "EstimatedMonthlySavingsPercentage": "31",
                            "UpfrontCost": "0",
                            "CurrencyCode": "USD",
                        }
                    ]
                }
            ]
        }


def test_collector_normalises_and_survives_missing_parts() -> None:
    data = collect(FakeCE(deny_reservations=True), date(2026, 11, 8))
    sp = data["savings_plans"]
    assert (
        sp["utilization_pct"] == 82
        and sp["unused_commitment"] == 180
        and sp["coverage_pct"] == 60.0
    )
    assert "reservations" not in data and any(
        "reservation_utilization" in e for e in data["errors"]
    )
    assert [r["plan_type"] for r in data["sp_recommendations"]] == ["COMPUTE_SP", "EC2_INSTANCE_SP"]
    ri = data["ri_recommendations"][0]
    assert (
        ri["instance_type"] == "db.r6g.large" and ri["count"] == 2 and ri["engine"] == "PostgreSQL"
    )


@pytest.fixture
def org(db: Database):
    org_id, owner = make_org(db, "Acme", "owner@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        conn = CloudConnection(
            org_id=org_id,
            provider="aws",
            name="Payer",
            status="active",
            external_id="ext",
            role_arn=ROLE,
            account_id="222222222222",
        )
        s.add(conn)
        s.flush()
        conn_id = conn.id
    aws = FakeAws(clients={"ce": FakeCE()})
    aws.allow(ROLE, "ext")
    with db.session() as s:
        assert snapshot(s, aws, conn_id) is not None
    return org_id, owner, conn_id, aws


def test_detectors_from_snapshot(client: TestClient, db: Database, org) -> None:
    org_id, owner, _, _ = org
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        run(s, org_id, only=["aws.sp_purchase", "aws.ri_purchase", "aws.commitment_underused"])
        rows = {f.detector + "/" + f.title: f for f in s.scalars(select(Finding))}
    sp = [f for k, f in rows.items() if k.startswith("aws.sp_purchase")]
    assert len(sp) == 2 and len({f.overlap_key for f in sp}) == 1
    assert any("Compute Savings Plan of 0.75 USD/hour (Payer)" in f.title for f in sp)
    ri = next(f for k, f in rows.items() if k.startswith("aws.ri_purchase"))
    assert (
        "Reserve 2 x PostgreSQL db.r6g.large (Relational Database Service, us-east-1)" in ri.title
    )
    under = next(f for k, f in rows.items() if k.startswith("aws.commitment_underused"))
    assert "Savings Plans only 82% used" in under.title and float(under.savings_monthly) == 0
    total = sign_in_client(client, db, owner, org_id).get("/api/v1/findings").json()["summary"]
    assert total["savings_monthly"] == pytest.approx(150.0 + 88.4, abs=0.01)  # best SP counted once


def test_api_and_refresh(client: TestClient, db: Database, org) -> None:
    org_id, owner, _, aws = org
    viewer = add_to_org(db, org_id, "viewer@acme.com", Role.VIEWER)
    v = sign_in_client(client, db, viewer, org_id)
    accounts = v.get("/api/v1/commitments").json()["accounts"]
    assert accounts[0]["name"] == "Payer" and accounts[0]["savings_plans"]["coverage_pct"] == 60.0
    assert v.post("/api/v1/commitments/refresh").status_code == 403
    client.app.state.aws = aws  # type: ignore[attr-defined]
    c = sign_in_client(client, db, owner, org_id)
    # The fake has no reservation APIs: the inventory part finds nothing and carries on.
    assert c.post("/api/v1/commitments/refresh").json() == {"snapshots": 1, "commitments": 0}
    assert len(c.get("/api/v1/commitments").json()["accounts"]) == 1  # latest per connection
    other_id, other = make_org(db, "Other", "o@other.com")
    assert (
        sign_in_client(client, db, other, other_id).get("/api/v1/commitments").json()["accounts"]
        == []
    )


def test_demo_commitments(client: TestClient, db: Database) -> None:
    from costlens.demo.seed import ensure_demo, load_all_seeders

    load_all_seeders()
    with db.session() as s:
        demo_id = ensure_demo(s).id
    _, visitor = make_org(db, "Visitor", "visitor@x.com")
    add_to_org(db, demo_id, "visitor@x.com", Role.VIEWER)
    v = sign_in_client(client, db, visitor, demo_id)
    assert (
        v.get("/api/v1/commitments").json()["accounts"][0]["savings_plans"]["coverage_pct"] == 58.0
    )
    detectors = {i["detector"] for i in v.get("/api/v1/findings").json()["items"]}
    assert {"aws.sp_purchase", "aws.ri_purchase"} <= detectors
