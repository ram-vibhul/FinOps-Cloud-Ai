from __future__ import annotations

import pytest
from sqlalchemy import select

from costlens.db import Database
from costlens.db.tenant import set_tenant
from costlens.findings import pipeline, service
from costlens.findings.registry import FindingIn
from costlens.findings.service import _store
from costlens.models import Finding
from tests.helpers import make_org


def _f(
    key: str, saving: float, rid: str = "i-1", rtype: str = "ec2_instance", **evidence
) -> FindingIn:
    return FindingIn(
        key=key,
        title=key,
        savings_monthly=saving,
        currency="USD",
        provider="aws",
        resource_id=rid,
        resource_type=rtype,
        evidence={"monthly_cost": 1000, **evidence},
    )


def _rows(s) -> dict[str, Finding]:
    return {f.dedup_key: f for f in s.scalars(select(Finding))}


def test_steps_stack_in_order_and_graviton_follows_the_new_size(db: Database) -> None:
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        _store(
            s,
            org_id,
            "aws.ec2_rightsizing",
            "rightsizing",
            [_f("size:i-1", 500, target="m6i.xlarge")],
        )
        _store(
            s,
            org_id,
            "aws.graviton",
            "rightsizing",
            [_f("graviton:i-1", 180, target="m7g.2xlarge")],
        )
        _store(s, org_id, "aws.previous_generation", "rightsizing", [_f("gen:i-1", 100)])
        _store(s, org_id, "aws.nonprod_schedule", "schedule", [_f("i-1", 640)])
        pipeline.apply(s, org_id, "USD")
        rows = _rows(s)
        summary = service.summary(list(rows.values()))
        again = pipeline.apply(s, org_id, "USD")  # idempotent: re-running changes nothing
        assert (
            again is None and float(_rows(s)["aws.graviton:graviton:i-1"].savings_monthly) == 90.0
        )
    size, grav = rows["aws.ec2_rightsizing:size:i-1"], rows["aws.graviton:graviton:i-1"]
    gen, sched = rows["aws.previous_generation:gen:i-1"], rows["aws.nonprod_schedule:i-1"]
    assert float(size.savings_monthly) == 500  # 50% of 1000
    assert float(grav.savings_monthly) == 90  # 18% of the 500 left
    assert grav.title == "After downsizing, move i-1 to Graviton m7g.xlarge"
    assert (
        float(gen.savings_monthly) == 0 and gen.evidence["pipeline"]["superseded_by"] == grav.title
    )
    assert float(sched.savings_monthly) == pytest.approx(410 * 0.64, abs=0.01)  # on the 410 left
    assert sched.evidence["pipeline"]["sized_on"] == 410.0
    assert summary["savings_monthly"] == pytest.approx(
        500 + 90 + 262.4, abs=0.01
    )  # adds up, never > cost


def test_removing_a_resource_replaces_everything_else(db: Database) -> None:
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        _store(s, org_id, "aws.ec2_rightsizing", "rightsizing", [_f("idle:i-1", 1000)])
        _store(s, org_id, "aws.graviton", "rightsizing", [_f("graviton:i-1", 180)])
        pipeline.apply(s, org_id, "USD")
        rows = _rows(s)
    assert float(rows["aws.graviton:graviton:i-1"].savings_monthly) == 0
    assert float(rows["aws.ec2_rightsizing:idle:i-1"].savings_monthly) == 1000
    assert service.summary(list(rows.values()))["savings_monthly"] == 1000


def test_savings_plans_are_sized_after_the_other_changes(db: Database) -> None:
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        _store(
            s,
            org_id,
            "aws.ec2_rightsizing",
            "rightsizing",
            [_f("size:i-1", 365, target="m6i.large")],
        )
        # 1 USD/hour at 27% off covers 1000 USD of On-Demand usage a month.
        _store(
            s,
            org_id,
            "aws.sp_purchase",
            "commitments",
            [
                FindingIn(
                    key="c:ComputeSp",
                    title="Buy a Compute Savings Plan of 1.00 USD/hour (Payer)",
                    savings_monthly=270,
                    currency="USD",
                    provider="aws",
                    evidence={"hourly_commitment": 1.0, "savings_pct": 27, "currency": "USD"},
                )
            ],
        )
        pipeline.apply(s, org_id, "USD")
        sp = _rows(s)["aws.sp_purchase:c:ComputeSp"]
    covered = 730 / 0.73
    left = (covered - 365) / covered
    assert float(sp.savings_monthly) == pytest.approx(270 * left, abs=0.01)
    assert sp.title == f"Buy a Compute Savings Plan of {left:.2f} USD/hour (Payer)"
    assert sp.evidence["pipeline"]["aws_hourly"] == 1.0
