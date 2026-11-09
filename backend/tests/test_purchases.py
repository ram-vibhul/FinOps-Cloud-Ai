from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from costlens.accounts.roles import Role
from costlens.costs import purchases
from costlens.costs.ingest import CostLine, replace_billing_period
from costlens.db import Database
from costlens.db.base import utcnow
from costlens.db.tenant import set_tenant
from costlens.findings.service import run
from costlens.models import Commitment, Finding, PlannedPurchase
from tests.helpers import add_to_org, make_org, sign_in_client


def _purchase(org_id, **kw) -> PlannedPurchase:
    base = {
        "kind": "savings_plan",
        "description": "SP",
        "hourly_commitment": 1.0,
        "term": "1yr",
        "payment": "no_upfront",
        "monthly_cost": 730.0,
        "currency": "USD",
        "planned_for": utcnow().date() + timedelta(days=10),
        "status": "planned",
        "verification": [],
    }
    return PlannedPurchase(org_id=org_id, **{**base, **kw})


def test_policy_violations(db: Database) -> None:
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    today = utcnow().date()
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        p = purchases.policy_for(s, org_id)
        assert purchases.violations(s, org_id, _purchase(org_id), steady_hourly=None) == []
        bad = _purchase(
            org_id, term="3yr", payment="all_upfront", planned_for=today + timedelta(days=2)
        )
        problems = purchases.violations(s, org_id, bad, steady_hourly=None)
        assert len(problems) == 3 and "3yr" in problems[0] and "7 days" in problems[2]
        p.max_monthly_commitment = 1000.0
        s.add(_purchase(org_id, monthly_cost=500.0))  # already planned
        s.flush()
        (limit,) = purchases.violations(
            s, org_id, _purchase(org_id, monthly_cost=600.0), steady_hourly=None
        )
        assert "1,100.00 USD" in limit
        p.max_monthly_commitment = None
        s.add(
            Commitment(
                org_id=org_id,
                kind="savings_plan",
                commitment_id="sp",
                description="Compute",
                state="active",
                hourly_commitment=2.0,
                alerts_sent=[],
                seen_at=utcnow(),
            )
        )
        s.flush()
        # Steady uncovered usage is worth 2/hour in commitment terms; active 2 + planned 1 + new 1.
        (cover,) = purchases.violations(s, org_id, _purchase(org_id), steady_hourly=2.0)
        assert "100% of steady usage" in cover


def test_advice_and_detector(db: Database) -> None:
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        common = {"state": "active", "alerts_sent": [], "seen_at": utcnow(), "currency": "USD"}
        s.add_all(
            [
                Commitment(
                    org_id=org_id,
                    kind="ec2_ri",
                    commitment_id="ri-conv",
                    description="m5.large",
                    offering_class="convertible",
                    recurring_hourly=0.1,
                    count=2,
                    utilization_pct=50,
                    **common,
                ),
                Commitment(
                    org_id=org_id,
                    kind="savings_plan",
                    commitment_id="sp-1",
                    description="Compute",
                    hourly_commitment=1.0,
                    utilization_pct=60,
                    **common,
                ),
                Commitment(
                    org_id=org_id,
                    kind="rds_ri",
                    commitment_id="rds-ok",
                    description="db.r6g.large",
                    recurring_hourly=0.3,
                    utilization_pct=99,
                    **common,
                ),
            ]
        )
        s.flush()
        run(s, org_id, only=["aws.commitment_actions"])
        rows = {f.dedup_key: f for f in s.scalars(select(Finding))}
    conv = rows["aws.commitment_actions:ri-conv"]
    assert conv.evidence["action"] == "exchange" and float(conv.savings_monthly) == pytest.approx(
        0.2 * 730 * 0.5
    )
    sp = rows["aws.commitment_actions:sp-1"]
    assert (
        sp.evidence["action"] == "use_plan" and float(sp.savings_monthly) == 0
    )  # cannot be recovered
    assert "aws.commitment_actions:rds-ok" not in rows


def test_lifecycle_and_verification(client: TestClient, db: Database, monkeypatch) -> None:
    sent: list = []
    monkeypatch.setattr("costlens.core.notify.notify", lambda db, event, m: sent.append(event) or 0)
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    member = add_to_org(db, org_id, "m@acme.com", Role.MEMBER)
    today = utcnow().date()
    body = {
        "kind": "savings_plan",
        "description": "Compute SP 1.00/hour",
        "hourly_commitment": 1.0,
        "term": "1yr",
        "payment": "no_upfront",
        "monthly_cost": 730,
        "estimated_monthly_saving": 270,
        "planned_for": (today + timedelta(days=8)).isoformat(),
    }
    m = sign_in_client(client, db, member, org_id)
    blocked = m.post("/api/v1/commitments/purchases", json={**body, "term": "3yr"})
    assert blocked.status_code == 422 and blocked.json()["code"] == "policy_violation"
    r = m.post("/api/v1/commitments/purchases", json=body)
    assert r.status_code == 201 and r.json()["status"] == "planned"
    pid = r.json()["id"]
    assert sent == ["purchase_planned"]
    assert (
        m.post(f"/api/v1/commitments/purchases/{pid}/approve").status_code == 403
    )  # admins approve
    o = sign_in_client(client, db, owner, org_id)
    assert (
        o.put(
            "/api/v1/commitments/policy",
            json={
                "max_term": "1yr",
                "allowed_payments": [],
                "max_coverage_pct": 80,
                "preview_days": 7,
            },
        ).status_code
        == 422
    )
    assert o.post(f"/api/v1/commitments/purchases/{pid}/approve").json()["status"] == "approved"
    early = o.post(
        f"/api/v1/commitments/purchases/{pid}/purchased",
        json={
            "commitment_id": "arn:aws:savingsplans::1:savingsplan/sp-9",
            "purchased_on": today.isoformat(),
        },
    )
    assert early.json()["code"] == "preview_not_over"
    bought_on = today + timedelta(days=8)
    r = o.post(
        f"/api/v1/commitments/purchases/{pid}/purchased",
        json={
            "commitment_id": "arn:aws:savingsplans::1:savingsplan/sp-9",
            "purchased_on": bought_on.isoformat(),
        },
    )
    assert r.json()["status"] == "purchased"
    # A month later the bill shows usage covered by the plan: 10 a day saved on 5 days.
    month = date(bought_on.year + (bought_on.month == 12), bought_on.month % 12 + 1, 1)
    lines = [
        CostLine(
            charge_date=month - timedelta(days=d),
            provider="aws",
            service_name="EC2",
            commitment_discount_id="arn:aws:savingsplans::1:savingsplan/sp-9",
            list_cost=Decimal(30),
            billed_cost=Decimal(0),
            effective_cost=Decimal(20),
            billing_currency="USD",
        )
        for d in range(1, 6)
    ]
    with db.session() as s:
        for period in sorted({ln.charge_date.replace(day=1) for ln in lines}):
            replace_billing_period(
                s,
                org_id=org_id,
                source_kind="t",
                source_key=str(period),
                billing_period=period,
                lines=[ln for ln in lines if ln.charge_date.replace(day=1) == period],
            )
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        assert purchases.verify(s, org_id, "USD", today=month + timedelta(days=1)) == 1
        assert (
            purchases.verify(s, org_id, "USD", today=month + timedelta(days=1)) == 0
        )  # once a month
        x = s.get(PlannedPurchase, uuid.UUID(pid))
        assert x is not None
        days_covered = sum(1 for ln in lines if ln.charge_date >= bought_on)
        assert (
            x.verification[0]["saving"] == 10.0 * days_covered
            and x.verification[0]["estimated"] == 270
        )
    cancel = o.post(f"/api/v1/commitments/purchases/{pid}/cancel")
    assert cancel.json()["code"] == "bad_status"
