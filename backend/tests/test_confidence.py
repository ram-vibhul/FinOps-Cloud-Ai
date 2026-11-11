from __future__ import annotations

import pytest
from sqlalchemy import select

from ccradar.db import Database
from ccradar.db.tenant import set_tenant
from ccradar.findings import service
from ccradar.findings.confidence import assess
from ccradar.findings.registry import FindingIn
from ccradar.findings.service import _store, run
from ccradar.models import Finding
from tests.helpers import make_org


def _f(detector: str, key: str = "x", **evidence) -> Finding:
    return Finding(
        detector=detector,
        dedup_key=f"{detector}:{key}",
        evidence=evidence,
        savings_monthly=100,
        risk="low",
        effort="low",
        confidence="medium",
    )


@pytest.mark.parametrize(
    ("finding", "level", "reason"),
    [
        (_f("aws.ebs_unattached", basis="billing"), "high", "priced from your bill"),
        (_f("aws.ebs_unattached", basis="list price"), "medium", "list prices"),
        (_f("aws.rds_rightsizing", "size:db", days=5, monthly_cost=10), "low", "only 5 days"),
        (_f("aws.rds_rightsizing", "size:db", days=10, monthly_cost=10), "medium", "10 days"),
        (
            _f(
                "aws.ec2_rightsizing",
                "size:i",
                days=14,
                monthly_cost=10,
                cpu_max=90,
                cpu_p95=30,
                mem_max=20,
            ),
            "medium",
            "spiky",
        ),
        (
            _f(
                "aws.ec2_rightsizing",
                "size:i",
                days=14,
                monthly_cost=10,
                cpu_max=30,
                cpu_p95=28,
                mem_max=None,
            ),
            "medium",
            "memory use is not measured",
        ),
        (_f("aws.nonprod_schedule", monthly_cost=10), "medium", "12 hours on weekdays"),
        (_f("aws.imported_advice", imported=True), "medium", "recommended by AWS"),
        (_f("k8s.rightsizing", hours=30), "low", "30 hours"),
        (_f("k8s.rightsizing", hours=400), "high", "17 days"),
        (
            _f(
                "aws.rds_rightsizing",
                "size:db",
                days=10,
                monthly_cost=10,
                also_recommended_by=[{"label": "AWS Compute Optimizer"}],
            ),
            "high",
            "AWS's own tools",
        ),
    ],
)
def test_assess(finding: Finding, level: str, reason: str) -> None:
    got, reasons = assess(finding)
    assert got == level and any(reason in r for r in reasons), reasons


def test_run_sets_confidence_and_ranking_uses_it(db: Database) -> None:
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        _store(
            s,
            org_id,
            "aws.ebs_unattached",
            "waste",
            [
                FindingIn(
                    key="vol-1",
                    title="v",
                    savings_monthly=100,
                    currency="USD",
                    evidence={"basis": "billing"},
                ),
                FindingIn(
                    key="vol-2",
                    title="v",
                    savings_monthly=100,
                    currency="USD",
                    evidence={"basis": "list price"},
                ),
            ],
        )
        run(s, org_id, only=["no-such-detector"])  # post-steps only
        rows = {f.dedup_key: f for f in s.scalars(select(Finding))}
    sure, unsure = rows["aws.ebs_unattached:vol-1"], rows["aws.ebs_unattached:vol-2"]
    assert sure.confidence == "high" and unsure.confidence == "medium"
    assert unsure.evidence["confidence"]["reasons"][0].startswith("priced from public list prices")
    assert service.score(sure) > service.score(unsure)
