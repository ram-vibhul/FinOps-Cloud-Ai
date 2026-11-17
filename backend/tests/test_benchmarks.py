"""Peer benchmarks (11.40): opt-in, ratios only, percentiles from at least five."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select

from ccradar.accounts.roles import Role
from ccradar.costs import benchmarks
from ccradar.costs.ingest import CostLine, replace_billing_period
from ccradar.db import Database
from ccradar.db.tenant import set_tenant
from ccradar.findings.registry import FindingIn
from ccradar.findings.service import _store
from ccradar.models import BenchmarkSample, Organization
from tests.helpers import add_to_org, make_org, sign_in_client

TODAY = date(2026, 11, 13)
OCT = date(2026, 10, 1)


def seed(db: Database, name: str, daily: float, list_daily: float, team: bool = True):
    org_id, owner = make_org(db, name, f"o@{name.lower()}.com")
    lines = [
        CostLine(
            charge_date=OCT + timedelta(days=d),
            provider="aws",
            service_name="Amazon Elastic Compute Cloud",
            usage_type="APS3-BoxUsage:m6i.xlarge",
            consumed_quantity=Decimal(24),
            billed_cost=Decimal(str(daily)),
            effective_cost=Decimal(str(daily)),
            list_cost=Decimal(str(list_daily)),
            billing_currency="USD",
            tags={"team": "web"} if team else {},
        )
        for d in range(31)
    ]
    with db.session() as s:
        replace_billing_period(
            s, org_id=org_id, source_kind="t", source_key="t", billing_period=OCT, lines=lines
        )
    return org_id, owner


def opt_in(s, org_id) -> None:
    org = s.get(Organization, org_id)
    assert org is not None
    org.settings = {"benchmarks": True}


def test_measure_ratios(db: Database) -> None:
    org_id, _ = seed(db, "Acme", daily=8, list_daily=10, team=False)
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        _store(s, org_id, "aws.ec2_idle", "waste", [
            FindingIn(key="i-1", title="Stop i-1", savings_monthly=24.8, currency="USD",
                      provider="aws", resource_id="i-1"),
        ])  # fmt: skip
        got = benchmarks.measure(s, org_id, TODAY)
    assert got["month"] == OCT and got["size_band"] == "small" and got["spend"] == 248
    m = got["metrics"]
    assert m["effective_savings_rate"] == 20.0  # 8 against a list price of 10
    assert m["waste_pct"] == 10.0  # 24.8 of 248
    assert m["allocated_pct"] == 0.0  # no team tag
    assert m["vcpu_hour_cost"] == round(8 / (24 * 4), 4)  # xlarge: 4 vCPUs


def test_peers_need_five_and_only_opted_in_share(db: Database) -> None:
    orgs = [seed(db, f"Org{i}", daily=10 - i, list_daily=10)[0] for i in range(6)]
    for org_id in orgs[:4]:
        with db.session() as s:
            set_tenant(s, org_id=org_id)
            opt_in(s, org_id)
            assert benchmarks.refresh(s, org_id, TODAY) is not None
    with db.session() as s:
        set_tenant(s, org_id=orgs[0])
        view = benchmarks.view(s, orgs[0], TODAY)
    assert view["opted_in"] and view["peers"]["organisations"] == 4
    assert view["peers"]["metrics"]["effective_savings_rate"] == {"n": 4}  # not enough yet

    with db.session() as s:  # a fifth opts in: percentiles appear
        set_tenant(s, org_id=orgs[4])
        opt_in(s, orgs[4])
        benchmarks.refresh(s, orgs[4], TODAY)
    with db.session() as s:
        set_tenant(s, org_id=orgs[0])
        rate = benchmarks.view(s, orgs[0], TODAY)["peers"]["metrics"]["effective_savings_rate"]
    assert rate == {"n": 5, "p25": 10.0, "median": 20.0, "p75": 30.0}

    with db.session() as s:  # not opted in: no peers shown, nothing shared
        set_tenant(s, org_id=orgs[5])
        view = benchmarks.view(s, orgs[5], TODAY)
        assert not view["opted_in"] and view["peers"] is None
        assert view["mine"]["effective_savings_rate"] == 50.0
        assert s.get(BenchmarkSample, orgs[5]) is None


def test_api_opt_in_and_out(client: TestClient, db: Database) -> None:
    org_id, owner = seed(db, "Acme", daily=8, list_daily=10)
    viewer = add_to_org(db, org_id, "v@acme.com", Role.VIEWER)
    v = sign_in_client(client, db, viewer, org_id)
    assert v.put("/api/v1/benchmarks/opt-in", json={"opted_in": True}).status_code == 403
    body = v.get("/api/v1/benchmarks").json()
    assert body["opted_in"] is False and body["min_peers"] == 5 and body["peers"] is None
    o = sign_in_client(client, db, owner, org_id)
    r = o.put("/api/v1/benchmarks/opt-in", json={"opted_in": True})
    assert r.status_code == 200, r.text
    assert r.json()["opted_in"] and r.json()["peers"]["organisations"] == 1
    with db.session() as s:
        stored = s.scalar(select(BenchmarkSample))
        assert stored is not None and set(stored.metrics) == set(benchmarks.METRICS)
    assert o.put("/api/v1/benchmarks/opt-in", json={"opted_in": False}).json()["peers"] is None
    with db.session() as s:  # stopping deletes what was shared
        assert s.scalar(select(BenchmarkSample)) is None
