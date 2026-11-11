from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ccradar.accounts.roles import Role
from ccradar.costs.ingest import CostLine, replace_billing_period
from ccradar.costs.tagrules import TagRule, apply_rules, canonical
from ccradar.db import Database
from ccradar.db.tenant import set_tenant
from ccradar.models import CostLineItem
from tests.helpers import add_to_org, make_org, sign_in_client

START = date(2026, 11, 1)


def lines() -> list[CostLine]:
    out = []
    for i in range(10):
        day = START + timedelta(days=i)

        def L(cost, service, tags, resource=None, category="Usage", day=day):
            return CostLine(
                charge_date=day,
                provider="aws",
                service_name=service,
                resource_id=resource,
                billed_cost=Decimal(str(cost)),
                effective_cost=Decimal(str(cost)),
                billing_currency="USD",
                charge_category=category,
                pricing_category=None if category == "Tax" else "Standard",
                tags=tags,
            )

        out += [
            L(50, "EC2", {"team": "payments", "project": "checkout", "env": "prod"}, "i-1"),
            L(20, "EC2", {"Team": "Payments", "env": "prod"}, "i-2"),
            L(20, "RDS", {"team": "payments ", "cost-center": "42"}, "db-1"),
            L(10, "S3", {"CostCenter": "42"}, "bucket-1"),
            L(5, "Tax", {}, category="Tax"),  # not taggable: excluded
        ]
    return out


def load(db: Database, org_id, items) -> None:
    with db.session() as s:
        replace_billing_period(
            s, org_id=org_id, source_kind="t", source_key="t", billing_period=START, lines=items
        )


@pytest.fixture
def env(client: TestClient, db: Database):
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    viewer = add_to_org(db, org_id, "v@acme.com", Role.VIEWER)
    load(db, org_id, lines())
    return {"c": client, "db": db, "org": org_id, "owner": owner, "viewer": viewer}


def test_canonical_and_rules() -> None:
    assert (
        canonical("CostCenter")
        == canonical("cost-center")
        == canonical("Cost Center")
        == "costcenter"
    )
    rules = [
        TagRule.model_validate({"kind": "key", "from": ["Team"], "to": "team"}),
        TagRule.model_validate(
            {"kind": "value", "key": "team", "from": ["Payments", "payments"], "to": "payments"}
        ),
    ]
    assert apply_rules({"Team": "Payments", "env": "prod"}, rules) == {
        "team": "payments",
        "env": "prod",
    }
    assert apply_rules({"team": "a", "Team": "b"}, rules) == {"team": "a"}  # existing key wins


def test_report(env) -> None:
    c = sign_in_client(env["c"], env["db"], env["viewer"], env["org"])
    r = c.get("/api/v1/costs/data-quality").json()
    assert r["taggable_cost"] == 1000.0  # tax excluded
    cov = {x["key"]: x for x in r["coverage"]}
    assert cov["team"]["untagged_cost"] == 300.0 and cov["team"]["coverage_pct"] == 70.0
    assert cov["project"]["coverage_pct"] == 50.0
    assert cov["team"]["top_untagged_services"][0] == {"service": "EC2", "cost": 200.0}
    assert r["unallocated"] == {"tag": "team", "cost": 300.0, "pct": 30.0}
    assert r["top_unallocated_resources"][0] == {
        "resource_id": "i-2",
        "service": "EC2",
        "cost": 200.0,
    }
    keys = {k["canonical"]: k for k in r["inconsistent_keys"]}
    assert {v["key"] for v in keys["team"]["variants"]} == {"team", "Team"}
    assert keys["team"]["suggested"] == "team"
    assert {v["key"] for v in keys["costcenter"]["variants"]} == {"cost-center", "CostCenter"}
    values = [v for v in r["inconsistent_values"] if v["key"] == "team"]
    assert values and {x["value"] for x in values[0]["variants"]} == {"payments", "payments "}


def test_rules_fix_existing_and_new_data(env) -> None:
    o = sign_in_client(env["c"], env["db"], env["owner"], env["org"])
    rules = {
        "rules": [
            {"kind": "key", "from": ["Team"], "to": "team"},
            {"kind": "key", "from": ["CostCenter", "cost-center"], "to": "cost_center"},
            {"kind": "value", "key": "team", "from": ["Payments", "payments "], "to": "payments"},
        ]
    }
    assert o.put("/api/v1/org/tag-rules", json=rules).status_code == 200
    assert o.post("/api/v1/org/tag-rules/apply").status_code == 202
    r = o.get("/api/v1/costs/data-quality").json()
    assert r["inconsistent_keys"] == [] and r["inconsistent_values"] == []
    assert {x["key"]: x["coverage_pct"] for x in r["coverage"]}["team"] == 90.0
    # New imports are normalised on the way in.
    load(env["db"], env["org"], lines())
    with env["db"].session() as s:
        set_tenant(s, org_id=env["org"])
        keys = {k for tags in s.scalars(select(CostLineItem.tags)) for k in tags}
    assert "Team" not in keys and "CostCenter" not in keys and "cost_center" in keys


def test_settings_and_permissions(env) -> None:
    v = sign_in_client(env["c"], env["db"], env["viewer"], env["org"])
    assert v.put("/api/v1/org/tag-rules", json={"rules": []}).status_code == 403
    assert (
        v.put("/api/v1/org/data-quality-settings", json={"required_tags": ["x"]}).status_code == 403
    )
    o = sign_in_client(env["c"], env["db"], env["owner"], env["org"])
    assert o.put(
        "/api/v1/org/data-quality-settings", json={"required_tags": ["owner", "team"]}
    ).json() == {"required_tags": ["owner", "team"]}
    assert [x["key"] for x in o.get("/api/v1/costs/data-quality").json()["coverage"]] == [
        "owner",
        "team",
    ]
    bad = o.put("/api/v1/org/data-quality-settings", json={"required_tags": ["bad'); --"]})
    assert bad.status_code == 400
    assert (
        o.put(
            "/api/v1/org/tag-rules", json={"rules": [{"kind": "value", "from": ["a"], "to": "b"}]}
        ).status_code
        == 400
    )
