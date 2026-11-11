from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from ccradar.accounts.roles import Role
from ccradar.db import Database
from ccradar.db.tenant import set_tenant
from ccradar.findings import targets
from ccradar.findings.registry import FindingIn
from ccradar.inventory.aws import Found
from ccradar.inventory.service import upsert
from ccradar.k8s.rightsizing import _advise
from ccradar.models import ReliabilityTarget
from tests.helpers import add_to_org, make_org, sign_in_client
from tests.test_k8s_rightsizing import stats

CLUSTER = str(uuid.UUID(int=7))


def _target(org_id, name, match, **kw) -> ReliabilityTarget:
    return ReliabilityTarget(org_id=org_id, name=name, match=match, **kw)


def _seed(db: Database) -> uuid.UUID:
    org_id, _ = make_org(db, "Acme", "owner@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        s.add_all(
            [
                _target(
                    org_id,
                    "shop ns",
                    {"kind": "k8s_namespace", "namespace": "shop"},
                    headroom_pct=30,
                ),
                _target(
                    org_id,
                    "checkout",
                    {
                        "kind": "k8s_workload",
                        "cluster_id": CLUSTER,
                        "namespace": "shop",
                        "workload": "checkout",
                    },
                    tier="critical",
                    headroom_pct=50,
                    min_replicas=3,
                ),
                _target(
                    org_id,
                    "loose",
                    {"kind": "k8s_namespace", "namespace": "batch"},
                    headroom_pct=5,
                    max_utilization_pct=95,
                ),
                _target(
                    org_id,
                    "payments",
                    {"kind": "aws_tag", "key": "Service", "value": "payments"},
                    tier="critical",
                    availability_pct=99.99,
                    max_utilization_pct=60,
                ),
                _target(
                    org_id,
                    "orders db",
                    {"kind": "aws_resource", "resource_id": "db-orders"},
                    availability_pct=99.95,
                ),
            ]
        )
        upsert(
            s,
            org_id,
            "1",
            [
                Found(
                    "Amazon Elastic Compute Cloud",
                    "ec2_instance",
                    rid,
                    "us-east-1",
                    state="running",
                    tags=tags,
                )
                for rid, tags in (("i-pay", {"service": "Payments"}), ("i-other", {}))
            ],
        )
    return org_id


def _f(key: str, rid: str, provider: str = "aws", **kw) -> FindingIn:
    return FindingIn(
        key=key,
        title=key,
        savings_monthly=10,
        currency="USD",
        provider=provider,
        resource_id=rid,
        **kw,
    )


def test_most_specific_target_wins_and_never_weakens_defaults(db: Database) -> None:
    org_id = _seed(db)
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        t = targets.Targets(s)
        assert t.for_workload(CLUSTER, "shop", "checkout").name == "checkout"
        assert t.for_workload(CLUSTER, "shop", "cart").headroom_pct == 30
        assert t.for_workload(CLUSTER, "web", "x").name is None
        loose = t.for_workload(CLUSTER, "batch", "job")
        assert loose.headroom_pct == 20 and loose.max_utilization_pct == 80  # only adds safety
        assert t.for_resource("i-pay", {"service": "Payments"}).name == "payments"  # any case
        assert t.for_resource("db-orders", {"service": "payments"}).name == "orders db"


def test_findings_respect_targets(db: Database) -> None:
    org_id = _seed(db)
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        out = targets.apply(
            s,
            "aws.ec2_rightsizing",
            [
                _f("idle:i-pay", "i-pay", commands=["aws ec2 stop-instances --instance-ids i-pay"]),
                _f("idle:i-other", "i-other"),
                _f("size:i-pay", "i-pay", evidence={"cpu_max": 35.0}),  # 70% > 60% ceiling
                _f("prev:i-pay", "i-pay", risk="low"),
            ],
        )
        keys = {i.key: i for i in out}
        assert set(keys) == {"idle:i-other", "prev:i-pay"}  # critical: no stop, no hot resize
        prev = keys["prev:i-pay"]
        assert prev.risk == "medium" and prev.evidence["reliability"]["target"] == "payments"
        assert "payments" in prev.description
        assert "reliability" not in keys["idle:i-other"].evidence
        multi = targets.apply(s, "aws.rds_multi_az_nonprod", [_f("db-orders", "db-orders")])
        assert multi == []  # 99.95% availability keeps the standby
        k8s = targets.apply(
            s, "k8s.rightsizing", [_f("x", f"{CLUSTER}/shop/Deployment/checkout", "kubernetes")]
        )
        assert k8s[0].risk == "medium" and k8s[0].evidence["reliability"]["min_replicas"] == 3


def test_headroom_changes_the_recommendation() -> None:
    assert _advise(stats(), 0.5)["recommended_cpu"] == 0.3  # 0.2 peak + 50%
    assert _advise(stats())["recommended_cpu"] == 0.24


def test_api_crud_and_permissions(client: TestClient, db: Database) -> None:
    org_id, owner = make_org(db, "Acme", "owner@acme.com")
    member = add_to_org(db, org_id, "dev@acme.com", Role.MEMBER)
    o = sign_in_client(client, db, owner, org_id)
    bad = o.post(
        "/api/v1/reliability-targets",
        json={"name": "x", "match": {"kind": "aws_tag", "key": "team"}},
    )
    assert bad.status_code == 422
    r = o.post(
        "/api/v1/reliability-targets",
        json={
            "name": "API",
            "tier": "critical",
            "headroom_pct": 40,
            "match": {"kind": "k8s_namespace", "namespace": "shop"},
        },
    )
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    body = {**r.json(), "min_replicas": 3}
    body.pop("id")
    assert o.put(f"/api/v1/reliability-targets/{tid}", json=body).json()["min_replicas"] == 3
    assert [t["name"] for t in o.get("/api/v1/reliability-targets").json()] == ["API"]
    m = sign_in_client(client, db, member, org_id)
    assert len(m.get("/api/v1/reliability-targets").json()) == 1
    assert m.delete(f"/api/v1/reliability-targets/{tid}").status_code == 403
    o = sign_in_client(client, db, owner, org_id)
    assert o.delete(f"/api/v1/reliability-targets/{tid}").status_code == 204
    with db.session() as s:
        set_tenant(s, bypass=True)
        assert s.scalars(select(ReliabilityTarget)).all() == []
