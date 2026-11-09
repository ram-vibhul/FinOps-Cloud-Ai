from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from costlens.db import Database
from costlens.db.base import utcnow
from costlens.db.tenant import set_tenant
from costlens.k8s.rightsizing import _advise
from costlens.k8s.service import ingest_objects
from costlens.k8s.usage import ingest_usage
from costlens.models import K8sCluster, K8sCost, K8sUsage
from tests.helpers import make_org, sign_in_client


def stats(**kw) -> dict:
    base = {
        "samples": 100,
        "cpu_sum": 10.0,
        "mem_sum": 10.0,
        "cpu_peak": 0.2,
        "mem_peak": 0.5,
        "hours": 48,
        "replicas": 2,
        "cpu_request": 1.0,
        "mem_request": 2.0,
        "cpu_limit": None,
        "mem_limit": None,
    }
    return {**base, **kw}


def test_rules_never_go_below_peak_plus_headroom() -> None:
    a = _advise(stats())
    assert a["action"] == "decrease"
    assert (
        a["recommended_cpu"] == 0.24 and a["recommended_memory_gib"] == 0.6094
    )  # 0.6 -> 16Mi steps
    assert a["recommended_cpu"] >= 0.2 * 1.2 and a["recommended_memory_gib"] >= 0.5 * 1.2
    # Small differences (<20%) are left alone.
    assert _advise(stats(cpu_peak=0.75, mem_peak=1.5))["action"] == "ok"
    # Peak above request: increase, never decrease the other resource below current.
    inc = _advise(stats(cpu_peak=1.5))
    assert (
        inc["action"] == "increase"
        and inc["recommended_cpu"] == 1.8
        and inc["recommended_memory_gib"] == 2.0
    )
    # Not enough data yet.
    assert _advise(stats(hours=5))["action"] == "learning"
    # No requests at all.
    assert _advise(stats(cpu_request=0, mem_request=0))["action"] == "set_requests"
    # Memory peak close to the limit: OOM risk, suggested limit at least peak + headroom.
    oom = _advise(stats(mem_peak=1.9, mem_limit=2.0))
    assert oom["oom_risk"] and oom["recommended_memory_limit_gib"] >= 1.9 * 1.2


def setup(db: Database, org_id) -> K8sCluster:
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        c = K8sCluster(
            id=uuid.uuid4(), org_id=org_id, name="prod", token_hash="x" * 64, token_hint="x"
        )
        s.add(c)
        s.flush()
        ingest_objects(
            s,
            c,
            "ReplicaSet",
            "s",
            [
                {
                    "uid": "rs",
                    "name": "api-5f",
                    "namespace": "shop",
                    "data": {"owners": [{"kind": "Deployment", "name": "api", "uid": "d"}]},
                }
            ],
            final=True,
        )
        pods = [
            {
                "uid": f"p{i}",
                "name": f"api-5f-{i}",
                "namespace": "shop",
                "data": {
                    "owners": [{"kind": "ReplicaSet", "name": "api-5f", "uid": "rs"}],
                    "nodeName": "n",
                    "phase": "Running",
                    "containers": [
                        {
                            "name": "api",
                            "image": "x",
                            "resources": {
                                "requests": {"cpu": "1", "memory": "2Gi"},
                                "limits": {"memory": "4Gi"},
                            },
                        }
                    ],
                },
            }
            for i in range(2)
        ]
        ingest_objects(s, c, "Pod", "s", pods, final=True)
        s.expunge(c)
        return c


def test_ingest_aggregates_per_workload_container_and_hour(db: Database) -> None:
    org_id, _ = make_org(db, "Acme", "owner@acme.com")
    c = setup(db, org_id)
    at = utcnow().replace(minute=5)
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        cl = s.merge(c)
        for cpu in (0.1, 0.3):
            assert (
                ingest_usage(
                    s,
                    cl,
                    at,
                    [
                        {
                            "namespace": "shop",
                            "pod": "api-5f-0",
                            "container": "api",
                            "cpu": cpu,
                            "memory": 0.5,
                        },
                        {
                            "namespace": "shop",
                            "pod": "api-5f-1",
                            "container": "api",
                            "cpu": cpu / 2,
                            "memory": 0.7,
                        },
                        {
                            "namespace": "shop",
                            "pod": "unknown",
                            "container": "x",
                            "cpu": 9,
                            "memory": 9,
                        },
                    ],
                )
                == 2
            )
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        row = s.scalars(select(K8sUsage)).one()
    assert (row.kind, row.name, row.container) == ("Deployment", "api", "api")
    assert row.samples == 4 and row.replicas == 2
    assert row.cpu_max == pytest.approx(0.3) and row.memory_max == pytest.approx(0.7)
    assert row.cpu_sum == pytest.approx(0.1 + 0.05 + 0.3 + 0.15)
    assert row.cpu_request == 1 and row.memory_request == 2 and row.memory_limit == 4


def test_api_recommendations_with_savings_and_usage_series(
    client: TestClient, db: Database
) -> None:
    org_id, owner = make_org(db, "Acme", "owner@acme.com")
    c = setup(db, org_id)
    now = utcnow()
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        cl = s.merge(c)
        for h in range(48):
            at = now - timedelta(hours=h)
            ingest_usage(
                s,
                cl,
                at,
                [
                    {
                        "namespace": "shop",
                        "pod": "api-5f-0",
                        "container": "api",
                        "cpu": 0.2,
                        "memory": 0.5,
                    }
                ],
            )
            s.add(
                K8sCost(
                    org_id=org_id,
                    cluster_id=cl.id,
                    hour=at.replace(minute=0, second=0, microsecond=0),
                    namespace="shop",
                    kind="Deployment",
                    name="api",
                    cpu_request=2,
                    memory_request=4,
                    cost=1,
                    currency="USD",
                    source="estimate",
                )
            )
    r = sign_in_client(client, db, owner, org_id).get("/api/v1/k8s/rightsizing").json()
    item = r["items"][0]
    assert item["workload"] == "api" and item["action"] == "decrease" and item["cluster"] == "prod"
    assert item["recommended_cpu"] == 0.24 and item["cpu_peak"] == 0.2
    # 1 USD an hour -> ~730 a month; requests shrink by most of their weight.
    assert item["workload_monthly_cost"] == pytest.approx(729.6, abs=1)
    assert 0 < item["monthly_saving"] < item["workload_monthly_cost"]
    assert r["monthly_saving"] == item["monthly_saving"] and r["headroom_pct"] == 20
    series = (
        sign_in_client(client, db, owner, org_id)
        .get(
            "/api/v1/k8s/usage",
            params={
                "cluster_id": str(c.id),
                "namespace": "shop",
                "kind": "Deployment",
                "name": "api",
            },
        )
        .json()
    )
    assert (
        len(series["containers"]["api"]) >= 48
        and series["containers"]["api"][0]["cpu_request"] == 1
    )
    other_id, other = make_org(db, "Other", "o@other.com")
    assert (
        sign_in_client(client, db, other, other_id).get("/api/v1/k8s/rightsizing").json()["items"]
        == []
    )


def test_agent_usage_endpoint_clamps_clock(client: TestClient, db: Database) -> None:
    from costlens.k8s.service import issue_token

    org_id, _ = make_org(db, "Acme", "owner@acme.com")
    c = setup(db, org_id)
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        cl = s.merge(c)
        token = issue_token(cl)
    client.cookies.clear()
    r = client.post(
        "/api/v1/agent/usage",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "sampled_at": "2020-01-01T00:00:00+00:00",
            "samples": [
                {
                    "namespace": "shop",
                    "pod": "api-5f-0",
                    "container": "api",
                    "cpu": 0.1,
                    "memory": 0.2,
                }
            ],
        },
    )
    assert r.json() == {"received": 1}
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        hour = s.scalars(select(K8sUsage.hour)).one()
    hour = hour if hour.tzinfo else hour.replace(tzinfo=utcnow().tzinfo)
    assert abs(hour - utcnow()) < timedelta(hours=2)  # not written into 2020


def test_demo_rightsizing_shows_each_kind_of_advice(client: TestClient, db: Database) -> None:
    from costlens.accounts.roles import Role
    from costlens.demo.seed import ensure_demo, load_all_seeders
    from tests.helpers import add_to_org

    load_all_seeders()
    with db.session() as s:
        org_id = ensure_demo(s).id
    _, user = make_org(db, "Visitor", "visitor@x.com")
    add_to_org(db, org_id, "visitor@x.com", Role.VIEWER)
    r = sign_in_client(client, db, user, org_id).get("/api/v1/k8s/rightsizing").json()
    by = {i["workload"]: i for i in r["items"]}
    assert (
        by["payments-gateway"]["action"] == "decrease"
        and by["payments-gateway"]["monthly_saving"] > 0
    )
    assert by["storefront"]["action"] == "increase"
    assert by["nightly-etl"]["oom_risk"]
    assert by["opensearch"]["action"] == "ok"
    assert r["items"][0]["workload"] == "nightly-etl"  # risks first
    assert r["monthly_saving"] > by["payments-gateway"]["monthly_saving"] * 0.99
