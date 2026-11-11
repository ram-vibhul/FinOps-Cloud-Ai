from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ccradar.accounts.roles import Role
from ccradar.costs.ingest import CostLine, replace_billing_period
from ccradar.db import Database
from ccradar.db.tenant import set_tenant
from ccradar.findings import registry, service
from ccradar.findings.registry import FindingIn
from ccradar.findings.workflow import measure, verify_applied
from ccradar.models import Finding, K8sCluster, K8sCost
from tests.helpers import add_to_org, make_org, sign_in_client

APPLIED = datetime(2026, 11, 15, 10, tzinfo=UTC)


def make_finding(db: Database, org_id, **kw) -> str:
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        f = Finding(
            org_id=org_id,
            detector="t",
            category="waste",
            dedup_key=f"t:{uuid.uuid4().hex}",
            overlap_key="x",
            title="Delete vol-x",
            savings_monthly=300,
            currency="USD",
            risk="low",
            effort="low",
            first_seen_at=APPLIED,
            last_seen_at=APPLIED,
            provider="aws",
            resource_id="vol-x",
            **kw,
        )
        s.add(f)
        s.flush()
        return str(f.id)


def test_transitions_owner_comments_history(client: TestClient, db: Database) -> None:
    org_id, owner = make_org(db, "Acme", "owner@acme.com")
    member = add_to_org(db, org_id, "dev@acme.com", Role.MEMBER)
    fid = make_finding(db, org_id)
    m = sign_in_client(client, db, member, org_id)
    r = m.patch(f"/api/v1/findings/{fid}", json={"status": "approved"})
    assert r.status_code == 403 and "Only admins" in r.json()["detail"]
    assert (
        m.patch(f"/api/v1/findings/{fid}", json={"status": "verified"}).status_code == 409
    )  # not from open
    assert (
        m.put(f"/api/v1/findings/{fid}/owner", json={"owner_id": str(uuid.uuid4())}).status_code
        == 400
    )
    assert m.put(f"/api/v1/findings/{fid}/owner", json={"owner_id": str(member)}).json()[
        "owner_id"
    ] == str(member)
    assert (
        m.post(
            f"/api/v1/findings/{fid}/comments", json={"text": "Snapshot taken, deleting Friday"}
        ).status_code
        == 201
    )
    o = sign_in_client(client, db, owner, org_id)
    approved = o.patch(
        f"/api/v1/findings/{fid}", json={"status": "approved", "note": "go ahead"}
    ).json()
    assert approved["status"] == "approved" and approved["approved_at"]
    m = sign_in_client(client, db, member, org_id)
    applied = m.patch(f"/api/v1/findings/{fid}", json={"status": "applied"}).json()
    assert applied["status"] == "applied" and applied["applied_at"]
    assert m.get("/api/v1/findings", params={"status": "active"}).json()["items"][0]["id"] == fid
    history = m.get(f"/api/v1/findings/{fid}/history").json()
    assert [h["kind"] for h in history] == ["owner", "comment", "status", "status"]
    assert history[1]["text"] == "Snapshot taken, deleting Friday" and history[1]["who"]
    assert (
        history[2]["data"] == {"from": "open", "to": "approved"}
        and history[2]["text"] == "go ahead"
    )


def bill(db: Database, org_id, resource: str, before: float, after: float) -> None:
    lines = []
    for d in range(30):
        day = APPLIED.date() - timedelta(days=30 - d)
        lines.append((day, before))
    for d in range(1, 11):
        lines.append((APPLIED.date() + timedelta(days=d), after))
    by_month: dict = {}
    for day, cost in lines:
        by_month.setdefault(day.replace(day=1), []).append(
            CostLine(
                charge_date=day,
                provider="aws",
                service_name="Amazon EC2",
                resource_id=resource,
                billed_cost=Decimal(str(cost)),
                effective_cost=Decimal(str(cost)),
                billing_currency="USD",
            )
        )
    with db.session() as s:
        for month, items in by_month.items():
            replace_billing_period(
                s, org_id=org_id, source_kind="t", source_key="t", billing_period=month, lines=items
            )


@pytest.mark.parametrize(
    ("after", "days_later", "status"),
    [
        (0.0, 9, "verified"),  # gone from the bill
        (0.0, 4, "applied"),  # too early
        (9.0, 9, "applied"),  # only 10% of the cost went away
    ],
)
def test_measure_and_verify_aws(db: Database, after: float, days_later: int, status: str) -> None:
    org_id, _ = make_org(db, "Acme", "owner@acme.com")
    bill(db, org_id, "vol-x", 10.0, after)
    fid = make_finding(db, org_id, status="applied", applied_at=APPLIED)
    now = APPLIED + timedelta(days=days_later, hours=12)
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        verify_applied(s, now)
        f = s.get(Finding, uuid.UUID(fid))
        assert f is not None and f.status == status
        assert f.measurement["before_daily"] == 10.0
        if status == "verified":
            assert float(f.realised_monthly or 0) == pytest.approx(304.0, abs=0.1) and f.verified_at


def test_measure_kubernetes_workload(db: Database) -> None:
    org_id, _ = make_org(db, "Acme", "owner@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        c = K8sCluster(
            id=uuid.uuid4(), org_id=org_id, name="prod", token_hash="x" * 64, token_hint="x"
        )
        s.add(c)
        s.flush()
        for h in range(24 * 20):
            hour = (APPLIED - timedelta(days=10)).replace(hour=0) + timedelta(hours=h)
            s.add(
                K8sCost(
                    org_id=org_id,
                    cluster_id=c.id,
                    hour=hour,
                    namespace="shop",
                    kind="Deployment",
                    name="api",
                    cpu_request=1,
                    memory_request=1,
                    currency="USD",
                    source="estimate",
                    cost=0.5 if hour < APPLIED else 0.2,
                )
            )
        s.flush()
        f = Finding(
            org_id=org_id,
            detector="k8s.rightsizing",
            category="kubernetes",
            dedup_key="k:1",
            overlap_key="w",
            title="Lower requests",
            savings_monthly=200,
            currency="USD",
            risk="low",
            effort="low",
            first_seen_at=APPLIED,
            last_seen_at=APPLIED,
            provider="kubernetes",
            resource_id=f"{c.id}/shop/Deployment/api",
            status="applied",
            applied_at=APPLIED,
        )
        s.add(f)
        s.flush()
        m = measure(s, f, APPLIED + timedelta(days=9))
    assert (
        m is not None
        and m["before_daily"] == pytest.approx(12.0)
        and m["after_daily"] == pytest.approx(4.8)
    )
    assert m["realised_monthly"] == pytest.approx((12.0 - 4.8) * 30.4, abs=0.1)


def test_detectors_leave_worked_on_findings_alone(db: Database) -> None:
    registry.load_all()
    saved = dict(registry.DETECTORS)
    registry.DETECTORS.clear()
    calls = {"n": 0}

    def detect(db_, org_id, currency):
        calls["n"] += 1
        return (
            [FindingIn(key="vol-x", title="Delete vol-x", savings_monthly=10, currency="USD")]
            if calls["n"] == 1
            else []
        )

    registry.DETECTORS["t.vol"] = registry.Detector("t.vol", "waste", "t", detect)
    try:
        org_id, _ = make_org(db, "Acme", "owner@acme.com")
        with db.session() as s:
            set_tenant(s, org_id=org_id)
            service.run(s, org_id)
            f = s.scalars(select(Finding)).one()
            f.status, f.applied_at = "applied", APPLIED
        with db.session() as s:
            set_tenant(s, org_id=org_id)
            service.run(s, org_id)  # the volume is gone now: the applied finding stays applied
            assert s.scalars(select(Finding)).one().status == "applied"
    finally:
        registry.DETECTORS.clear()
        registry.DETECTORS.update(saved)


def test_summary_counts_realised(client: TestClient, db: Database) -> None:
    org_id, owner = make_org(db, "Acme", "owner@acme.com")
    make_finding(
        db, org_id, status="verified", realised_monthly=280, applied_at=APPLIED, verified_at=APPLIED
    )
    s = sign_in_client(client, db, owner, org_id).get("/api/v1/findings").json()["summary"]
    assert s["realised_monthly"] == 280.0 and s["verified_count"] == 1
