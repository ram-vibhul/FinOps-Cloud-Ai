from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from ccradar.costs.ingest import CostLine, replace_billing_period
from ccradar.db import Database
from ccradar.db.base import utcnow
from ccradar.db.tenant import set_tenant
from ccradar.findings.registry import FindingIn
from ccradar.findings.service import _store
from ccradar.models import Budget
from tests.helpers import make_org, sign_in_client


def test_executive_summary(client: TestClient, db: Database) -> None:
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    today = utcnow().date()
    lines = [
        CostLine(
            charge_date=today - timedelta(days=d),
            provider="aws",
            service_name="Amazon EC2",
            billed_cost=Decimal(100),
            effective_cost=Decimal(100),
            billing_currency="USD",
        )
        for d in range(1, 40)
    ]
    with db.session() as s:
        for month in sorted({ln.charge_date.replace(day=1) for ln in lines}):
            replace_billing_period(
                s,
                org_id=org_id,
                source_kind="t",
                source_key=str(month),
                billing_period=month,
                lines=[ln for ln in lines if ln.charge_date.replace(day=1) == month],
            )
        set_tenant(s, org_id=org_id)
        s.add(Budget(org_id=org_id, name="Everything", amount=100, currency="USD", alert_state={}))
        _store(
            s,
            org_id,
            "aws.ebs_unattached",
            "waste",
            [
                FindingIn(
                    key="v", title="Delete v", savings_monthly=40, currency="USD", resource_id="v"
                )
            ],
        )
    c = sign_in_client(client, db, owner, org_id)
    e = c.get("/api/v1/roi/executive").json()
    assert e["spend"]["month_to_date"] > 0 and e["spend"]["forecast"] >= e["spend"]["month_to_date"]
    assert e["budgets"]["at_risk"][0]["name"] == "Everything"
    assert e["savings"]["open_monthly"] == 40 and e["savings"]["this_week_monthly"] == 40
    assert e["movers"] is not None and e["policy_violations"] == 0 and e["unit_cost"] is None
