from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from costlens.accounts.roles import Role
from costlens.costs.ingest import CostLine, replace_billing_period
from costlens.costs.vtags import Definition, Rule, compute
from costlens.db import Database
from costlens.db.tenant import set_tenant
from costlens.models import CostLineItem
from tests.helpers import add_to_org, make_org, sign_in_client

START = date(2026, 11, 1)
TEAM = {
    "key": "team",
    "default_value": "unallocated",
    "rules": [
        {"value": "data", "match": {"account": ["444"]}},
        {"value": "payments", "match": {"service": ["RDS"], "region": ["ap-south-1"]}},
        {"value": "platform", "match": {"resource_contains": ["nat-", "shared"]}},
    ],
}


def L(day, cost, service, account, *, tags=None, resource=None, region="ap-south-1"):
    return CostLine(
        charge_date=day,
        provider="aws",
        service_name=service,
        sub_account_id=account,
        region=region,
        resource_id=resource,
        billed_cost=Decimal(str(cost)),
        effective_cost=Decimal(str(cost)),
        billing_currency="USD",
        tags=tags or {},
    )


def lines():
    out = []
    for i in range(5):
        d = START + timedelta(days=i)
        out += [
            L(d, 10, "Redshift", "444"),
            L(d, 8, "RDS", "222"),
            L(d, 5, "RDS", "222", region="us-east-1"),
            L(d, 3, "VPC", "222", resource="nat-0abc"),
            L(d, 4, "EC2", "222", tags={"team": "search"}),
            L(d, 2, "S3", "222"),
        ]
    return out


def load(db, org_id):
    with db.session() as s:
        replace_billing_period(
            s, org_id=org_id, source_kind="t", source_key="t", billing_period=START, lines=lines()
        )


def test_rule_semantics() -> None:
    d = Definition.model_validate(TEAM)
    assert compute(
        [d], {"sub_account_id": "444", "service_name": "RDS", "region": "ap-south-1"}
    ) == {"team": "data"}
    assert compute([d], {"sub_account_id": "1", "service_name": "RDS", "region": "us-east-1"}) == {
        "team": "unallocated"
    }
    assert compute([d], {"resource_id": "x", "resource_name": "Shared-Proxy"}) == {
        "team": "platform"
    }
    assert compute([d], {"sub_account_id": "444", "tags": {"team": "search"}}) == {"team": "search"}
    over = d.model_copy(update={"override_existing": True})
    assert compute([over], {"sub_account_id": "444", "tags": {"team": "search"}}) == {
        "team": "data"
    }
    tagged = Definition(
        key="env",
        rules=[
            Rule.model_validate({"value": "prod", "match": {"tag": {"stage": ["live", "prod"]}}})
        ],
    )
    assert compute([tagged], {"tags": {"stage": "live"}}) == {"env": "prod"}
    assert compute([tagged], {"tags": {}}) == {}  # no default
    empty = Definition(key="x", rules=[Rule.model_validate({"value": "y", "match": {}})])
    assert compute([empty], {"service_name": "EC2"}) == {}  # an empty rule never matches everything


@pytest.fixture
def env(client: TestClient, db: Database):
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    viewer = add_to_org(db, org_id, "v@acme.com", Role.VIEWER)
    load(db, org_id)
    return {"c": client, "db": db, "org": org_id, "owner": owner, "viewer": viewer}


def explore(c, **body):
    return c.post(
        "/api/v1/costs/explore",
        json={"start": "2026-11-01", "end": "2026-11-05", "granularity": "total", **body},
    ).json()


def test_define_recompute_and_explore(env) -> None:
    o = sign_in_client(env["c"], env["db"], env["owner"], env["org"])
    preview = o.post("/api/v1/virtual-tags/preview", json={"definition": TEAM}).json()
    assert {v["value"]: v["cost"] for v in preview["values"]} == {
        "data": 50.0,
        "payments": 40.0,
        "unallocated": 35.0,
        "search": 20.0,
        "platform": 15.0,
    }
    assert preview["lines_by_source"]["tag"] == 5
    created = o.post("/api/v1/virtual-tags", json=TEAM)
    assert created.status_code == 201
    r = explore(o, group_by=["vtag:team"])
    assert {g["label"]: g["total"] for g in r["groups"]} == {
        "data": 50.0,
        "payments": 40.0,
        "unallocated": 35.0,
        "search": 20.0,
        "platform": 15.0,
    }
    # New imports get virtual tags immediately.
    load(env["db"], env["org"])
    with env["db"].session() as s:
        set_tenant(s, org_id=env["org"])
        vt = {tuple(sorted(v.items())) for v in s.scalars(select(CostLineItem.vtags))}
    assert (("team", "data"),) in vt
    # Changing the definition recomputes existing data.
    changed = {**TEAM, "rules": TEAM["rules"][1:], "default_value": "nobody"}
    o.put(f"/api/v1/virtual-tags/{created.json()['id']}", json=changed)
    assert {g["label"] for g in explore(o, group_by=["vtag:team"])["groups"]} >= {"nobody"}
    assert o.post("/api/v1/virtual-tags", json=TEAM).status_code == 409


def test_team_alias_can_point_at_virtual_tag(env) -> None:
    o = sign_in_client(env["c"], env["db"], env["owner"], env["org"])
    o.post("/api/v1/virtual-tags", json=TEAM)
    o.put(
        "/api/v1/org/dimension-settings", json={"team_tag": "vtag:team", "project_tag": "project"}
    )
    labels = {g["label"] for g in explore(o, group_by=["team"])["groups"]}
    assert "unallocated" in labels and "(untagged)" not in labels
    dq = o.get("/api/v1/costs/data-quality").json()
    assert dq["unallocated"]["cost"] == 0.0  # every line has a team via the virtual tag


def test_validation_and_permissions(env) -> None:
    v = sign_in_client(env["c"], env["db"], env["viewer"], env["org"])
    assert v.get("/api/v1/virtual-tags").json() == []
    assert v.post("/api/v1/virtual-tags", json=TEAM).status_code == 403
    assert v.post("/api/v1/virtual-tags/preview", json={"definition": TEAM}).status_code == 200
    o = sign_in_client(env["c"], env["db"], env["owner"], env["org"])
    bad = {**TEAM, "rules": [{"value": "x", "match": {"tag": {"bad'); --": ["a"]}}}]}
    assert o.post("/api/v1/virtual-tags", json=bad).status_code == 422
    assert o.post("/api/v1/virtual-tags", json={**TEAM, "key": ""}).status_code == 422
    rid = o.post("/api/v1/virtual-tags", json=TEAM).json()["id"]
    assert o.delete(f"/api/v1/virtual-tags/{rid}").status_code == 204
    r = explore(o, group_by=["vtag:team"])
    assert {g["label"] for g in r["groups"]} == {"(untagged)"}  # recomputed after delete
