from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from costlens.accounts.roles import Role
from costlens.clouds.aws import commitment_inventory as ci
from costlens.costs.ingest import CostLine, replace_billing_period
from costlens.db import Database
from costlens.db.base import utcnow
from costlens.db.tenant import set_tenant
from costlens.models import CloudConnection, Commitment, Contract
from tests.fakes.aws import FakeAws
from tests.helpers import add_to_org, make_org, sign_in_client

ROLE = "arn:aws:iam::111111111111:role/costlens"
SP_ARN = "arn:aws:savingsplans::111111111111:savingsplan/sp-1"


class FakeSp:
    def describe_savings_plans(self, **kw: Any) -> dict[str, Any]:
        return {
            "savingsPlans": [
                {
                    "savingsPlanArn": SP_ARN,
                    "savingsPlanId": "sp-1",
                    "savingsPlanType": "Compute",
                    "state": "active",
                    "start": "2025-12-01T00:00:00.000Z",
                    "end": "2026-12-01T00:00:00.000Z",
                    "commitment": "4.8",
                    "currency": "USD",
                    "termDurationInSeconds": 31536000,
                    "paymentOption": "No Upfront",
                }
            ]
        }


class FakeEc2:
    def __init__(self) -> None:
        self.items = [
            {
                "ReservedInstancesId": "ri-1",
                "InstanceType": "m6i.xlarge",
                "InstanceCount": 4,
                "Start": datetime(2024, 1, 1, tzinfo=UTC),
                "End": datetime(2027, 1, 1, tzinfo=UTC),
                "Duration": 94608000,
                "FixedPrice": 6200.0,
                "OfferingClass": "standard",
                "OfferingType": "Partial Upfront",
                "State": "active",
                "RecurringCharges": [{"Amount": 0.071, "Frequency": "Hourly"}],
            }
        ]

    def describe_reserved_instances(self, **kw: Any) -> dict[str, Any]:
        return {"ReservedInstances": self.items}


class FakeRds:
    def describe_reserved_db_instances(self, **kw: Any) -> dict[str, Any]:
        return {
            "ReservedDBInstances": [
                {
                    "ReservedDBInstanceId": "rdb-1",
                    "DBInstanceClass": "db.r6g.large",
                    "DBInstanceCount": 1,
                    "StartTime": datetime(2025, 6, 1, tzinfo=UTC),
                    "Duration": 31536000,
                    "FixedPrice": 0.0,
                    "State": "active",
                    "OfferingType": "No Upfront",
                    "RecurringCharges": [{"RecurringChargeAmount": 0.31}],
                }
            ]
        }


class Empty:
    def __getattr__(self, name: str):
        key = {
            "describe_reserved_cache_nodes": "ReservedCacheNodes",
            "describe_reserved_instances": "ReservedInstances",
            "describe_reserved_nodes": "ReservedNodes",
        }[name]
        return lambda **kw: {key: []}


class FakeCe:
    def get_savings_plans_utilization_details(self, **kw: Any) -> dict[str, Any]:
        return {
            "SavingsPlansUtilizationDetails": [
                {"SavingsPlanArn": SP_ARN, "Utilization": {"UtilizationPercentage": "96.4"}}
            ]
        }

    def get_reservation_utilization(self, **kw: Any) -> dict[str, Any]:
        return {
            "UtilizationsByTime": [
                {
                    "Groups": [
                        {
                            "Key": "SUBSCRIPTION_ID",
                            "Value": "123",
                            "Attributes": {"leaseId": "ri-1"},
                            "Utilization": {"UtilizationPercentage": "71.5"},
                        }
                    ]
                }
            ]
        }


def test_parsers() -> None:
    (sp,) = ci.savings_plans(FakeSp())
    assert (
        sp.hourly_commitment == 4.8
        and sp.ends_at == datetime(2026, 12, 1, tzinfo=UTC)
        and sp.term_years == 1.0
    )
    (ri,) = ci.ec2_reservations(FakeEc2(), "eu-west-1")
    assert (
        ri.count == 4
        and ri.recurring_hourly == 0.071
        and ri.term_years == 3.0
        and ri.upfront == 6200.0
    )
    (rdb,) = ci.rds_reservations(FakeRds(), "eu-west-1")
    assert rdb.ends_at == datetime(2026, 6, 1, tzinfo=UTC)  # start + duration
    assert rdb.recurring_hourly == 0.31


def _setup(db: Database):
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        conn = CloudConnection(
            org_id=org_id,
            provider="aws",
            name="Payer",
            status="active",
            external_id="x",
            role_arn=ROLE,
            account_id="111111111111",
        )
        s.add(conn)
        s.flush()
        conn_id = conn.id
    ec2 = FakeEc2()
    aws = FakeAws(
        clients={
            "savingsplans": FakeSp(),
            "ec2": ec2,
            "rds": FakeRds(),
            "elasticache": Empty(),
            "opensearch": Empty(),
            "redshift": Empty(),
            "ce": FakeCe(),
        }
    )
    aws.allow(ROLE, "x")
    return org_id, owner, conn_id, aws, ec2


def test_collect_utilisation_and_retirement(db: Database) -> None:
    org_id, _, conn_id, aws, ec2 = _setup(db)
    with db.session() as s:
        assert ci.collect(s, aws, conn_id, regions=["eu-west-1"]) == {"count": 3, "errors": {}}
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        rows = {c.kind: c for c in s.scalars(select(Commitment))}
    assert rows["savings_plan"].utilization_pct == 96.4 and rows["ec2_ri"].utilization_pct == 71.5
    assert rows["rds_ri"].utilization_pct is None
    ec2.items = []  # the reservation ended
    with db.session() as s:
        ci.collect(s, aws, conn_id, regions=["eu-west-1"])
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        states = {c.kind: c.state for c in s.scalars(select(Commitment))}
    assert states == {"savings_plan": "active", "ec2_ri": "retired", "rds_ri": "active"}


def test_expiry_alerts_once_per_threshold(db: Database, monkeypatch) -> None:
    sent: list[Any] = []
    monkeypatch.setattr(
        ci,
        "notify",
        lambda db, event, messages: sent.extend((event, m) for m in messages) or len(messages),
    )
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    today = date(2026, 11, 8)
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        s.add(
            Commitment(
                org_id=org_id,
                kind="savings_plan",
                commitment_id="sp",
                description="Compute",
                state="active",
                ends_at=datetime(2026, 11, 30, tzinfo=UTC),
                alerts_sent=[],
                seen_at=utcnow(),
            )
        )
        s.add(
            Contract(
                org_id=org_id,
                name="EDP",
                commitment_amount=1000,
                starts_on=date(2025, 1, 1),
                ends_on=date(2027, 6, 1),
                alerts_sent=[],
            )
        )
        s.flush()
        assert ci.expiry_alerts(s, today) == 1
        assert ci.expiry_alerts(s, today) == 0  # not twice
        assert (
            ci.expiry_alerts(s, today + timedelta(days=16)) == 1
        )  # 6 days left: the 7-day reminder
    assert [m.title for _, m in sent] == [
        "Savings Plan Compute ends in 22 days",
        "Savings Plan Compute ends in 6 days",
    ]
    assert {e for e, _ in sent} == {"commitment_expiring"}


def test_contract_progress_and_api(client: TestClient, db: Database) -> None:
    org_id, owner, _, _, _ = _setup(db)
    member = add_to_org(db, org_id, "m@acme.com", Role.MEMBER)
    today = utcnow().date()
    start = today - timedelta(days=9)
    with db.session() as s:
        lines = [
            CostLine(
                charge_date=start + timedelta(days=d),
                provider="aws",
                service_name="EC2",
                billed_cost=Decimal(100),
                effective_cost=Decimal(90),
                billing_currency="USD",
            )
            for d in range(10)
        ]
        for month in sorted({ln.charge_date.replace(day=1) for ln in lines}):
            replace_billing_period(
                s,
                org_id=org_id,
                source_kind="t",
                source_key=str(month),
                billing_period=month,
                lines=[ln for ln in lines if ln.charge_date.replace(day=1) == month],
            )
    o = sign_in_client(client, db, owner, org_id)
    body = {
        "name": "EDP",
        "commitment_amount": 10000,
        "starts_on": start.isoformat(),
        "ends_on": (start + timedelta(days=99)).isoformat(),
    }
    assert (
        o.post(
            "/api/v1/commitments/contracts", json={**body, "ends_on": start.isoformat()}
        ).status_code
        == 422
    )
    r = o.post("/api/v1/commitments/contracts", json=body)
    assert r.status_code == 201, r.text
    p = r.json()["progress"]
    assert (
        p["spent"] == 1000 and p["expected_pct"] == 10.0 and p["forecast"] == pytest.approx(10000)
    )
    assert p["shortfall"] == 0
    m = sign_in_client(client, db, member, org_id)
    assert m.post("/api/v1/commitments/contracts", json=body).status_code == 403
    inv = m.get("/api/v1/commitments/inventory").json()
    assert [c["name"] for c in inv["contracts"]] == ["EDP"] and inv["kinds"][
        "ec2_ri"
    ] == "EC2 reservation"
    o = sign_in_client(client, db, owner, org_id)
    assert o.delete(f"/api/v1/commitments/contracts/{r.json()['id']}").status_code == 204
