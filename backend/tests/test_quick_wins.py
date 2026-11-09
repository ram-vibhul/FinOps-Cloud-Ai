from __future__ import annotations

from fastapi.testclient import TestClient

from costlens.db import Database
from costlens.db.tenant import set_tenant
from costlens.findings.registry import FindingIn
from costlens.findings.service import _store
from tests.helpers import make_org, sign_in_client


def test_plan_buckets(client: TestClient, db: Database) -> None:
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    items = [
        FindingIn(
            key=f"easy-{i}",
            title=f"easy {i}",
            savings_monthly=100 + i,
            currency="USD",
            risk="low",
            effort="low",
            resource_id=f"r-easy-{i}",
        )
        for i in range(12)
    ] + [
        FindingIn(
            key="mid",
            title="mid",
            savings_monthly=500,
            currency="USD",
            risk="medium",
            effort="medium",
            resource_id="r-mid",
        ),
        FindingIn(
            key="big",
            title="big",
            savings_monthly=900,
            currency="USD",
            risk="high",
            effort="high",
            resource_id="r-big",
        ),
        FindingIn(
            key="zero", title="replaced", savings_monthly=0, currency="USD", resource_id="r-zero"
        ),
    ]
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        _store(s, org_id, "aws.ebs_unattached", "waste", items)
        _store(
            s,
            org_id,
            "aws.graviton",
            "rightsizing",
            [
                FindingIn(
                    key="unsure",
                    title="unsure",
                    savings_monthly=300,
                    currency="USD",
                    risk="low",
                    effort="low",
                    resource_id="r-unsure",
                )
            ],
        )
        from sqlalchemy import select

        from costlens.models import Finding

        unsure = s.scalars(select(Finding).where(Finding.detector == "aws.graviton")).one()
        unsure.confidence = "low"
    c = sign_in_client(client, db, owner, org_id)
    plan = {b["key"]: b for b in c.get("/api/v1/findings/plan").json()["buckets"]}
    week = plan["this_week"]
    assert (
        week["count"] == 10 and week["items"][0]["title"] == "easy 11"
    )  # best first, capped at 10
    month = {i["title"] for i in plan["this_month"]["items"]}
    assert month == {"mid", "easy 0", "easy 1"}  # overflow from this week joins this month
    later = {i["title"] for i in plan["later"]["items"]}
    assert later == {"big", "unsure"}  # high effort/risk, or low confidence
    assert all(i["title"] != "replaced" for b in plan.values() for i in b["items"])
    assert week["savings_monthly"] == sum(100 + i for i in range(2, 12))
