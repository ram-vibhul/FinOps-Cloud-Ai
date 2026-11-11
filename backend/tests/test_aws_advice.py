from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from ccradar.clouds.aws import advice
from ccradar.db import Database
from ccradar.db.base import utcnow
from ccradar.db.tenant import set_tenant
from ccradar.findings.registry import FindingIn
from ccradar.findings.service import _store, run
from ccradar.models import CloudConnection, ExternalRecommendation, Finding
from tests.fakes.aws import FakeAws
from tests.helpers import make_org, sign_in_client

ROLE = "arn:aws:iam::111111111111:role/ccradar"


class OptIn(Exception):
    def __init__(self) -> None:
        super().__init__("opt in")
        self.response = {"Error": {"Code": "OptInRequiredException"}}


class FakeHub:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def list_recommendations(self, **kw: Any) -> dict[str, Any]:
        if self.fail:
            raise OptIn()
        return {
            "items": [
                {
                    "recommendationId": "r1",
                    "actionType": "Rightsize",
                    "currentResourceType": "Ec2Instance",
                    "resourceId": "i-1",
                    "resourceArn": "arn:aws:ec2:us-east-1:1:instance/i-1",
                    "estimatedMonthlySavings": 55.5,
                    "currencyCode": "USD",
                    "implementationEffort": "Low",
                },
                {
                    "recommendationId": "r2",
                    "actionType": "PurchaseSavingsPlans",
                    "estimatedMonthlySavings": 900,
                    "currencyCode": "USD",
                },
            ]
        }


class FakeOptimizer:
    def get_ec2_instance_recommendations(self, **kw: Any) -> dict[str, Any]:
        return {
            "instanceRecommendations": [
                {
                    "instanceArn": "arn:aws:ec2:us-east-1:1:instance/i-1",
                    "finding": "Overprovisioned",
                    "recommendationOptions": [
                        {
                            "instanceType": "m6i.large",
                            "savingsOpportunity": {
                                "estimatedMonthlySavings": {"value": 40, "currency": "USD"}
                            },
                        },
                        {
                            "instanceType": "t3.large",
                            "savingsOpportunity": {
                                "estimatedMonthlySavings": {"value": 60, "currency": "USD"}
                            },
                        },
                    ],
                },
                {
                    "instanceArn": "arn:aws:ec2:us-east-1:1:instance/i-2",
                    "finding": "Optimized",
                    "recommendationOptions": [],
                },
            ]
        }

    def get_ebs_volume_recommendations(self, **kw: Any) -> dict[str, Any]:
        return {"volumeRecommendations": []}

    def get_lambda_function_recommendations(self, **kw: Any) -> dict[str, Any]:
        return {
            "lambdaFunctionRecommendations": [
                {
                    "functionArn": "arn:aws:lambda:us-east-1:1:function:checkout:$LATEST",
                    "finding": "NotOptimized",
                    "memorySizeRecommendationOptions": [
                        {
                            "memorySize": 512,
                            "savingsOpportunity": {
                                "estimatedMonthlySavings": {"value": 12.5, "currency": "USD"}
                            },
                        }
                    ],
                },
            ]
        }


class FakeAdvisor:
    def list_recommendations(self, **kw: Any) -> dict[str, Any]:
        assert kw["pillar"] == "cost_optimizing"
        return {
            "recommendationSummaries": [
                {
                    "arn": "arn:ta:1",
                    "name": "Low Utilization Amazon EC2 Instances",
                    "status": "warning",
                },
                {"arn": "arn:ta:2", "name": "Idle Load Balancers", "status": "ok"},
            ]
        }

    def list_recommendation_resources(self, **kw: Any) -> dict[str, Any]:
        return {
            "recommendationResourceSummaries": [
                {
                    "arn": "arn:ta:1:i-9",
                    "awsResourceId": "i-9",
                    "regionCode": "us-east-1",
                    "metadata": {"Estimated Monthly Savings": "$1,234.50"},
                }
            ]
        }


def test_parsers() -> None:
    hub = list(advice.cost_optimization_hub(FakeHub()))
    assert [(a.action, a.resource_id, a.savings_monthly) for a in hub] == [
        ("rightsize", "i-1", 55.5),
        ("purchase_savings_plan", None, 900.0),
    ]
    co = list(advice.compute_optimizer(FakeOptimizer(), "us-east-1"))
    assert [(a.resource_id, a.savings_monthly) for a in co] == [
        ("i-1", 60.0),
        ("arn:aws:lambda:us-east-1:1:function:checkout", 12.5),
    ]  # best option; version dropped
    (ta,) = advice.trusted_advisor(FakeAdvisor())
    assert ta.action == "stop" and ta.resource_id == "i-9" and ta.savings_monthly == 1234.5


def _org_with_connection(db: Database):
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        conn = CloudConnection(
            org_id=org_id,
            provider="aws",
            name="Prod",
            status="active",
            external_id="x",
            role_arn=ROLE,
            account_id="111111111111",
        )
        s.add(conn)
        s.flush()
        return org_id, owner, conn.id


def _aws(hub_fails: bool = False) -> FakeAws:
    aws = FakeAws(
        clients={
            "cost-optimization-hub": FakeHub(hub_fails),
            "compute-optimizer": FakeOptimizer(),
            "trustedadvisor": FakeAdvisor(),
        }
    )
    aws.allow(ROLE, "x")
    return aws


def test_collect_keeps_going_when_a_source_is_off(db: Database) -> None:
    org_id, _, conn_id = _org_with_connection(db)
    with db.session() as s:
        result = advice.collect(s, _aws(), conn_id, regions=["us-east-1"])
    assert result == {"count": 5, "errors": {}}
    with db.session() as s:
        result = advice.collect(s, _aws(hub_fails=True), conn_id, regions=["us-east-1"])
    assert result["errors"] == {"cost_optimization_hub": "OptInRequiredException"}
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        sources = sorted(r.source for r in s.scalars(select(ExternalRecommendation)))
        conn = s.get(CloudConnection, conn_id)
        assert conn is not None and conn.settings["advice"]["errors"] == result["errors"]
    # The hub failed this time: its earlier advice is kept, not wiped.
    assert sources.count("cost_optimization_hub") == 2 and sources.count("compute_optimizer") == 2


def test_merge_by_resource_and_action(db: Database) -> None:
    org_id, _, conn_id = _org_with_connection(db)
    with db.session() as s:
        advice.collect(s, _aws(), conn_id, regions=["us-east-1"])
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        _store(
            s,
            org_id,
            "aws.ec2_rightsizing",
            "rightsizing",
            [
                FindingIn(
                    key="size:i-1",
                    title="Downsize i-1",
                    savings_monthly=70,
                    currency="USD",
                    provider="aws",
                    resource_id="i-1",
                )
            ],
        )
        _store(
            s,
            org_id,
            "aws.sp_purchase",
            "commitments",
            [
                FindingIn(
                    key="sp",
                    title="Buy a Savings Plan",
                    savings_monthly=800,
                    currency="USD",
                    provider="aws",
                )
            ],
        )
        run(s, org_id, only=["aws.imported_advice"])
        rows = {f.dedup_key: f for f in s.scalars(select(Finding))}
    ours = rows["aws.ec2_rightsizing:size:i-1"]
    labels = sorted(x["label"] for x in ours.evidence["also_recommended_by"])
    assert labels == ["AWS Compute Optimizer", "AWS Cost Optimization Hub"]
    assert "also_recommended_by" in rows["aws.sp_purchase:sp"].evidence  # matched by action alone
    imported = {k: f for k, f in rows.items() if k.startswith("aws.imported_advice:")}
    assert set(imported) == {
        "aws.imported_advice:rightsize:arn:aws:lambda:us-east-1:1:function:checkout",
        "aws.imported_advice:remove:i-9",
    }
    ta = imported["aws.imported_advice:remove:i-9"]
    assert (
        float(ta.savings_monthly) == 1234.5
        and ta.evidence["sources"][0]["label"] == "AWS Trusted Advisor"
    )
    assert ta.category == "imported"


def test_api(client: TestClient, db: Database) -> None:
    org_id, owner, _ = _org_with_connection(db)
    client.app.state.aws = _aws()  # type: ignore[attr-defined]
    c = sign_in_client(client, db, owner, org_id)
    r = c.post("/api/v1/findings/advice/refresh")
    assert r.status_code == 200 and r.json()["imported"] == 5
    status = c.get("/api/v1/findings/advice").json()
    counts = {x["source"]: x["count"] for x in status["sources"]}
    assert counts == {"cost_optimization_hub": 2, "compute_optimizer": 2, "trusted_advisor": 1}
    assert status["connections"][0]["count"] == 5
    assert status["connections"][0]["fetched_at"] <= utcnow().isoformat()
