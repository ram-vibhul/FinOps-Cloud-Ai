from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from costlens.accounts.roles import Role
from costlens.costs.ingest import CostLine, replace_billing_period
from costlens.db import Database
from costlens.db.base import utcnow
from costlens.db.tenant import set_tenant
from costlens.k8s import quantity
from costlens.k8s.allocation import allocate, due_clusters
from costlens.k8s.service import ingest_objects
from costlens.models import K8sCluster, K8sCost
from tests.helpers import add_to_org, make_org, sign_in_client

HOUR = datetime(2026, 11, 4, 10, tzinfo=UTC)
CPU_RATE, GIB_RATE = 0.031611, 0.004237


def test_quantities() -> None:
    assert (
        quantity.cores("250m") == 0.25 and quantity.cores("2") == 2 and quantity.cores(1.5) == 1.5
    )
    assert quantity.gib("512Mi") == 0.5 and quantity.gib("2Gi") == 2
    assert quantity.parse("1e9") == 1e9 and quantity.parse("2G") == 2e9
    assert quantity.parse("rubbish") == 0 and quantity.parse(None) == 0


def node(uid: str, name: str, cpu: str, mem: str, provider: str = "") -> dict:
    res = {"cpu": cpu, "memory": mem}
    return {
        "uid": uid,
        "name": name,
        "data": {"providerID": provider, "capacity": res, "allocatable": res},
    }


def pod(uid, name, node_name, cpu, mem, owner=None, phase="Running", ns="shop"):
    req = {k: v for k, v in (("cpu", cpu), ("memory", mem)) if v}
    data = {
        "nodeName": node_name,
        "phase": phase,
        "containers": [{"name": "c", "image": "x", "resources": {"requests": req}}],
    }
    if owner:
        data["owners"] = [owner]
    return {"uid": uid, "name": name, "namespace": ns, "data": data}


def setup(db: Database, org_id) -> K8sCluster:
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        c = K8sCluster(
            id=uuid.uuid4(),
            org_id=org_id,
            name="prod",
            token_hash="x" * 64,
            token_hint="x",
            last_seen_at=utcnow(),
        )
        s.add(c)
        s.flush()
        ingest_objects(
            s,
            c,
            "Node",
            "s1",
            [
                node("n1", "node-1", "4", "16Gi", "aws:///eu-west-1a/i-0aaa"),
                node("n2", "node-2", "2", "8Gi", "aws:///eu-west-1b/i-0bbb"),  # not in the bill
            ],
            final=True,
        )
        ingest_objects(
            s,
            c,
            "ReplicaSet",
            "s1",
            [
                {
                    "uid": "rs1",
                    "name": "web-7d9f",
                    "namespace": "shop",
                    "data": {"owners": [{"kind": "Deployment", "name": "web", "uid": "d1"}]},
                }
            ],
            final=True,
        )
        ingest_objects(
            s,
            c,
            "Job",
            "s1",
            [
                {
                    "uid": "j1",
                    "name": "nightly-123",
                    "namespace": "batch",
                    "data": {"owners": [{"kind": "CronJob", "name": "nightly", "uid": "cj1"}]},
                }
            ],
            final=True,
        )
        rs = {"kind": "ReplicaSet", "name": "web-7d9f", "uid": "rs1"}
        ingest_objects(
            s,
            c,
            "Pod",
            "s1",
            [
                pod("p1", "web-7d9f-a", "node-1", "1", "2Gi", rs),
                pod("p2", "web-7d9f-b", "node-1", "1000m", "2048Mi", rs),
                pod(
                    "p3",
                    "nightly-123-x",
                    "node-2",
                    "500m",
                    "1Gi",
                    {"kind": "Job", "name": "nightly-123", "uid": "j1"},
                    ns="batch",
                ),
                pod("p4", "pending", "", "8", "64Gi", phase="Pending"),  # not scheduled
                pod("p5", "done", "node-1", "2", "4Gi", phase="Succeeded"),
                pod("p6", "besteffort", "node-1", None, None),
            ],
            final=True,
        )
        cluster_id = c.id
    # Bill: node-1 costs 24 USD a day = 1 USD an hour.
    with db.session() as s:
        replace_billing_period(
            s,
            org_id=org_id,
            source_kind="t",
            source_key="t",
            billing_period=date(2026, 11, 1),
            lines=[
                CostLine(
                    charge_date=date(2026, 11, d),
                    provider="aws",
                    service_name="Amazon EC2",
                    resource_id="i-0aaa",
                    billed_cost=Decimal(24),
                    effective_cost=Decimal(24),
                    billing_currency="USD",
                )
                for d in (1, 2, 3)
            ],
        )
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        cluster = s.get(K8sCluster, cluster_id)
        assert cluster is not None
        s.expunge(cluster)
        return cluster


def costs(db: Database, org_id) -> dict:
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        return {(r.kind, r.name): r for r in s.scalars(select(K8sCost))}


def test_allocation_by_requests_with_billing_and_estimates(db: Database) -> None:
    org_id, _ = make_org(db, "Acme", "owner@acme.com")
    cluster = setup(db, org_id)
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        totals = allocate(s, s.merge(cluster), HOUR)
    rows = costs(db, org_id)
    # node-1 billed 1.00/h; CPU part = 4*cpu_rate / (4*cpu_rate + 16*gib_rate)
    cpu_part = 4 * CPU_RATE / (4 * CPU_RATE + 16 * GIB_RATE)
    web = 2 * (cpu_part * 1 / 4 + (1 - cpu_part) * 2 / 16)
    assert float(rows[("Deployment", "web")].cost) == pytest.approx(web, abs=1e-5)
    assert rows[("Deployment", "web")].source == "billing"
    assert (
        rows[("Deployment", "web")].cpu_request == 2
        and rows[("Deployment", "web")].memory_request == 4
    )
    assert float(rows[("Idle", "node-1")].cost) == pytest.approx(1 - web, abs=1e-5)
    # node-2 has no bill line: estimated from rates (USD org, so no conversion).
    node2 = 2 * CPU_RATE + 8 * GIB_RATE
    nightly = CPU_RATE * 2 * 0.5 / 2 + GIB_RATE * 8 * 1 / 8
    assert float(rows[("CronJob", "nightly")].cost) == pytest.approx(nightly, abs=1e-5)
    assert rows[("CronJob", "nightly")].source == "estimate"
    assert float(rows[("Idle", "node-2")].cost) == pytest.approx(node2 - nightly, abs=1e-5)
    assert rows[("Idle", "node-2")].cpu_request == pytest.approx(1.5)  # free cores
    # Best-effort pod (no requests) costs nothing; pending/succeeded pods are skipped.
    assert float(rows[("Pod", "besteffort")].cost) == 0
    assert ("Pod", "pending") not in rows and ("Pod", "done") not in rows
    assert totals["allocated"] + totals["idle"] == pytest.approx(1 + node2, abs=1e-5)
    # Re-running the same hour replaces instead of doubling.
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        allocate(s, s.merge(cluster), HOUR + timedelta(minutes=30))
    assert len(costs(db, org_id)) == len(rows)


def test_overcommitted_requests_never_exceed_node_cost(db: Database) -> None:
    org_id, _ = make_org(db, "Acme", "owner@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        c = K8sCluster(
            id=uuid.uuid4(), org_id=org_id, name="tiny", token_hash="x" * 64, token_hint="x"
        )
        s.add(c)
        s.flush()
        ingest_objects(s, c, "Node", "s", [node("n", "n", "1", "1Gi")], final=True)
        ingest_objects(
            s,
            c,
            "Pod",
            "s",
            [pod("a", "a", "n", "1", "1Gi"), pod("b", "b", "n", "1", "1Gi")],
            final=True,
        )
        totals = allocate(s, c, HOUR)
    assert totals["allocated"] == pytest.approx(CPU_RATE + GIB_RATE, abs=1e-6)
    assert totals["idle"] == 0


def test_api_breakdowns_and_isolation(client: TestClient, db: Database) -> None:
    org_id, _ = make_org(db, "Acme", "owner@acme.com")
    cluster = setup(db, org_id)
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        for h in range(3):
            allocate(s, s.merge(cluster), HOUR + timedelta(hours=h))
    viewer = add_to_org(db, org_id, "viewer@acme.com", Role.VIEWER)
    c = sign_in_client(client, db, viewer, org_id)
    r = c.get("/api/v1/k8s/costs").json()
    node2 = 2 * CPU_RATE + 8 * GIB_RATE
    assert r["total"] == pytest.approx(3 * (1 + node2), abs=0.01) and r["hours"] == 3
    assert r["allocated"] + r["idle"] == pytest.approx(r["total"], abs=0.01)
    assert 0 < r["idle_pct"] < 100 and 0 < r["estimated_pct"] < 20
    labels = {i["label"]: i for i in r["items"]}
    assert set(labels) == {"shop", "batch", "Idle capacity"} and labels["Idle capacity"]["idle"]
    assert r["daily"][0]["day"] == "2026-11-04"
    w = c.get("/api/v1/k8s/costs", params={"group_by": "workload", "namespace": "shop"}).json()[
        "items"
    ]
    assert [i["label"] for i in w] == ["web", "besteffort"] and w[0]["kind"] == "Deployment"
    assert w[0]["cpu"] == 2.0 and w[0]["memory_gib"] == 4.0  # average requests
    idle = c.get("/api/v1/k8s/costs", params={"group_by": "node"}).json()["items"]
    assert {i["label"] for i in idle} == {"node-1", "node-2"} and idle[0]["cluster"] == "prod"
    by_cluster = c.get("/api/v1/k8s/costs", params={"group_by": "cluster"}).json()["items"]
    assert by_cluster[0]["label"] == "prod" and by_cluster[0]["share_pct"] == 100.0
    other_id, other = make_org(db, "Other", "o@other.com")
    o = sign_in_client(client, db, other, other_id)
    assert o.get("/api/v1/k8s/costs").json()["total"] == 0


def test_silent_clusters_are_not_allocated(db: Database) -> None:
    org_id, _ = make_org(db, "Acme", "owner@acme.com")
    cluster = setup(db, org_id)
    now = utcnow()
    with db.session() as s:
        set_tenant(s, bypass=True)
        assert [c.id for c in due_clusters(s, now)] == [cluster.id]
        assert due_clusters(s, now + timedelta(hours=3)) == []


def test_allocation_job(db: Database, settings) -> None:
    from costlens.jobs.context import JobContext, configure_jobs
    from costlens.jobs.k8s import allocate_costs

    org_id, _ = make_org(db, "Acme", "owner@acme.com")
    setup(db, org_id)
    configure_jobs(JobContext(settings=settings, db=db, storage=None))  # type: ignore[arg-type]
    assert allocate_costs() == 1
    assert costs(db, org_id)


def test_demo_cluster_has_costs_idle_and_a_crash_loop(client: TestClient, db: Database) -> None:
    from costlens.demo.seed import ensure_demo, load_all_seeders, seeders
    from costlens.models import K8sEvent

    load_all_seeders()
    if "k8s.cluster" not in seeders():
        pytest.fail("k8s demo seeder not registered")
    with db.session() as s:
        org_id = ensure_demo(s).id
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        assert s.scalars(select(K8sEvent).where(K8sEvent.reason == "BackOff")).first() is not None
    _, user = make_org(db, "Visitor", "visitor@x.com")
    add_to_org(db, org_id, "visitor@x.com", Role.VIEWER)
    c = sign_in_client(client, db, user, org_id)
    r = c.get("/api/v1/k8s/costs").json()
    assert r["hours"] == 30 * 24 and len(r["daily"]) >= 30
    assert 15 < r["idle_pct"] < 60
    assert {i["label"] for i in r["items"]} >= {"checkout", "payments", "search", "Idle capacity"}
    assert c.get("/api/v1/clusters").json()[0]["name"] == "prod-ap-south-1"


def test_custom_prices_node_fee_spot_and_currency(client: TestClient, db: Database) -> None:
    from costlens.costs import fx

    org_id, owner = make_org(db, "OnPrem", "ops@onprem.example")
    with db.session() as s:
        set_tenant(s, bypass=True)
        fx.store(s, {HOUR.date(): {"EUR": Decimal(1), "USD": Decimal("1.10")}}, until=HOUR.date())
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        c = K8sCluster(
            id=uuid.uuid4(),
            org_id=org_id,
            name="dc1",
            token_hash="x" * 64,
            token_hint="x",
            settings={
                "prices": {
                    "currency": "EUR",
                    "cpu_hour": 0.02,
                    "gib_hour": 0.003,
                    "node_hour": 0.05,
                    "spot_discount_pct": 60,
                }
            },
        )
        s.add(c)
        s.flush()
        ingest_objects(
            s,
            c,
            "Node",
            "s",
            [
                node("n1", "rack-1", "4", "16Gi"),
                {
                    **node("n2", "spot-1", "2", "8Gi"),
                    "data": {
                        **node("n2", "spot-1", "2", "8Gi")["data"],
                        "labels": {"cloud.google.com/gke-spot": "true"},
                    },
                },
            ],
            final=True,
        )
        allocate(s, c, HOUR)
    rows = costs(db, org_id)
    rack = (4 * 0.02 + 16 * 0.003 + 0.05) * 1.10
    spot = (2 * 0.02 + 8 * 0.003 + 0.05) * 0.4 * 1.10
    assert float(rows[("Idle", "rack-1")].cost) == pytest.approx(rack, abs=1e-6)
    assert float(rows[("Idle", "spot-1")].cost) == pytest.approx(spot, abs=1e-6)
    assert rows[("Idle", "rack-1")].source == "price" and rows[("Idle", "rack-1")].currency == "USD"
    r = sign_in_client(client, db, owner, org_id).get("/api/v1/k8s/costs").json()
    assert r["estimated_pct"] == 0  # your own prices are not "estimated from list prices"


def test_always_use_prices_overrides_the_bill(db: Database) -> None:
    org_id, _ = make_org(db, "Acme", "owner@acme.com")
    cluster = setup(db, org_id)
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        c = s.merge(cluster)
        c.settings = {
            "prices": {"currency": "USD", "cpu_hour": 0.01, "gib_hour": 0.001, "always": True}
        }
        allocate(s, c, HOUR)
    rows = costs(db, org_id)
    node1 = 4 * 0.01 + 16 * 0.001  # not the billed 1.00/h
    web = rows[("Deployment", "web")]
    assert web.source == "price"
    assert float(web.cost) + float(rows[("Idle", "node-1")].cost) == pytest.approx(node1, abs=1e-6)


def test_prices_api(client: TestClient, db: Database) -> None:
    org_id, owner = make_org(db, "Acme", "owner@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        c = K8sCluster(
            id=uuid.uuid4(), org_id=org_id, name="dc1", token_hash="x" * 64, token_hint="x"
        )
        s.add(c)
        cid = str(c.id)
    viewer = add_to_org(db, org_id, "viewer@acme.com", Role.VIEWER)
    body = {
        "currency": "INR",
        "cpu_hour": 2.5,
        "gib_hour": 0.35,
        "node_hour": 4,
        "spot_discount_pct": 70,
    }
    assert (
        sign_in_client(client, db, viewer, org_id)
        .put(f"/api/v1/clusters/{cid}/prices", json=body)
        .status_code
        == 403
    )
    c = sign_in_client(client, db, owner, org_id)
    assert (
        c.put(f"/api/v1/clusters/{cid}/prices", json={**body, "spot_discount_pct": 99}).status_code
        == 422
    )
    assert (
        c.put(f"/api/v1/clusters/{cid}/prices", json={**body, "currency": "rupees"}).status_code
        == 422
    )
    saved = c.put(f"/api/v1/clusters/{cid}/prices", json=body).json()
    assert saved["prices"] == {**body, "always": False}
    assert c.delete(f"/api/v1/clusters/{cid}/prices").json()["prices"] is None
