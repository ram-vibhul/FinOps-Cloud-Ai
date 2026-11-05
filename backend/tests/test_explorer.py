from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from costlens.accounts.roles import Role
from costlens.costs.ingest import CostLine, replace_billing_period
from costlens.db import Database
from tests.helpers import add_to_org, make_org, sign_in_client

START, END = date(2026, 11, 1), date(2026, 11, 10)


def line(day, cost, service, *, tags=None, resource=None, region="ap-south-1", account="1"):
    return CostLine(
        charge_date=day,
        provider="aws",
        service_name=service,
        sub_account_id=account,
        region=region,
        resource_id=resource,
        resource_name=f"name-{resource}" if resource else None,
        resource_type="EC2 instance" if resource else None,
        billed_cost=Decimal(str(cost)),
        effective_cost=Decimal(str(cost)),
        billing_currency="USD",
        tags=tags or {},
    )


@pytest.fixture
def env(client: TestClient, db: Database):
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    member = add_to_org(db, org_id, "m@acme.com", Role.MEMBER)
    viewer = add_to_org(db, org_id, "v@acme.com", Role.VIEWER)
    lines = []
    for i in range(10):
        day = START + timedelta(days=i)
        lines += [
            line(day, 10, "EC2", tags={"team": "payments", "env": "prod"}, resource="i-1"),
            line(day, 6, "EC2", tags={"team": "search"}, resource="i-2", region="us-east-1"),
            line(day, 4, "S3", tags={"env": "prod"}),
            line(day, 2, "Lambda"),
        ]
    prev = [
        line(START - timedelta(days=i + 1), 5, "EC2", tags={"team": "payments"}, resource="i-1")
        for i in range(10)
    ]
    with db.session() as s:
        replace_billing_period(
            s, org_id=org_id, source_kind="t", source_key="t", billing_period=START, lines=lines
        )
        replace_billing_period(
            s,
            org_id=org_id,
            source_kind="t",
            source_key="t",
            billing_period=date(2026, 10, 1),
            lines=prev,
        )
    return {
        "c": client,
        "db": db,
        "org": org_id,
        "owner": owner,
        "member": member,
        "viewer": viewer,
    }


def explore(env, who="viewer", **body):
    c = sign_in_client(env["c"], env["db"], env[who], env["org"])
    payload = {"start": START.isoformat(), "end": END.isoformat(), **body}
    r = c.post("/api/v1/costs/explore", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def test_group_by_service_uses_rollup(env) -> None:
    r = explore(env, group_by=["service"], granularity="total")
    assert r["source"] == "daily"
    assert [(g["label"], g["total"]) for g in r["groups"]] == [
        ("EC2", 160.0),
        ("S3", 40.0),
        ("Lambda", 20.0),
    ]
    assert r["groups"][0]["share"] == pytest.approx(72.7)
    assert r["groups"][0]["previous"] == 50.0 and r["groups"][0]["change_pct"] == 220.0


def test_group_by_tag_and_untagged(env) -> None:
    r = explore(env, group_by=["tag:team"], granularity="total")
    assert r["source"] == "line_items"
    assert {g["label"]: g["total"] for g in r["groups"]} == {
        "payments": 100.0,
        "search": 60.0,
        "(untagged)": 60.0,
    }


def test_team_alias_follows_settings(env) -> None:
    assert {g["label"] for g in explore(env, group_by=["team"], granularity="total")["groups"]} == {
        "payments",
        "search",
        "(untagged)",
    }
    c = sign_in_client(env["c"], env["db"], env["owner"], env["org"])
    assert (
        c.put(
            "/api/v1/org/dimension-settings", json={"team_tag": "env", "project_tag": "project"}
        ).status_code
        == 200
    )
    labels = {g["label"] for g in explore(env, group_by=["team"], granularity="total")["groups"]}
    assert labels == {"prod", "(untagged)"}


def test_two_dimensions_and_filters(env) -> None:
    r = explore(
        env, group_by=["service", "region"], granularity="total", filters={"tag:env": ["prod"]}
    )
    assert {g["label"]: g["total"] for g in r["groups"]} == {
        "EC2 · ap-south-1": 100.0,
        "S3 · ap-south-1": 40.0,
    }
    untagged = explore(
        env, group_by=["service"], granularity="total", filters={"tag:team": ["(untagged)"]}
    )
    assert {g["label"] for g in untagged["groups"]} == {"S3", "Lambda"}


def test_daily_series_with_other(env) -> None:
    r = explore(env, group_by=["service"], granularity="day", top=1)
    assert len(r["periods"]) == 10
    assert [s["label"] for s in r["series"]] == ["EC2", "Other (2)"]
    assert r["series"][1]["values"][0] == 6.0
    monthly = explore(env, group_by=["service"], granularity="month")
    assert monthly["periods"] == ["2026-11"]


def test_cost_per_resource(env) -> None:
    r = explore(env, group_by=["resource"], granularity="total")
    top = r["groups"][0]
    assert top["key"] == ["i-1"] and top["total"] == 100.0
    assert top["resource"] == {
        "name": "name-i-1",
        "type": "EC2 instance",
        "service": "EC2",
        "region": "ap-south-1",
        "account": "1",
    }


def test_validation(env) -> None:
    c = sign_in_client(env["c"], env["db"], env["viewer"], env["org"])
    bad = c.post(
        "/api/v1/costs/explore",
        json={"start": "2026-11-01", "end": "2026-11-02", "group_by": ["colour"]},
    )
    assert bad.status_code == 422
    injection = c.post(
        "/api/v1/costs/explore",
        json={"start": "2026-11-01", "end": "2026-11-02", "group_by": ["tag:x') OR 1=1 --"]},
    )
    assert injection.status_code == 422
    too_long = c.post("/api/v1/costs/explore", json={"start": "2025-01-01", "end": "2026-11-02"})
    assert too_long.status_code == 400


def test_values_and_tag_keys(env) -> None:
    c = sign_in_client(env["c"], env["db"], env["viewer"], env["org"])
    services = c.get("/api/v1/costs/dimensions/service/values").json()
    assert [v["value"] for v in services] == ["EC2", "S3", "Lambda"]
    teams = c.get("/api/v1/costs/dimensions/tag:team/values", params={"q": "pay"}).json()
    assert teams == [{"value": "payments", "total": 150.0}]
    keys = {k["key"]: k for k in c.get("/api/v1/costs/tags", params={"days": 60}).json()}
    assert set(keys) == {"team", "env"}
    assert keys["team"]["coverage_pct"] == pytest.approx(210 / 270 * 100, abs=0.1)
    assert c.get("/api/v1/costs/dimensions/colour/values").status_code == 400


def test_saved_views(env) -> None:
    query = {"group_by": ["team"], "granularity": "day", "days": 30}
    m = sign_in_client(env["c"], env["db"], env["member"], env["org"])
    private = m.post("/api/v1/views", json={"name": "My teams", "query": query}).json()
    shared = m.post(
        "/api/v1/views", json={"name": "Team costs", "shared": True, "query": query}
    ).json()
    assert private["mine"] is True
    assert (
        m.post("/api/v1/views", json={"name": "Bad", "query": {"group_by": ["colour"]}}).status_code
        == 400
    )
    v = sign_in_client(env["c"], env["db"], env["viewer"], env["org"])
    assert [x["name"] for x in v.get("/api/v1/views").json()] == ["Team costs"]
    assert v.post("/api/v1/views", json={"name": "x", "query": query}).status_code == 403
    o = sign_in_client(env["c"], env["db"], env["owner"], env["org"])
    assert o.delete(f"/api/v1/views/{private['id']}").status_code == 404  # private stays private
    renamed = o.put(
        f"/api/v1/views/{shared['id']}", json={"name": "Teams", "shared": True, "query": query}
    )
    assert renamed.json()["name"] == "Teams"  # admins may edit shared views
    other, boss = make_org(env["db"], "Other", "b@other.com")
    b = sign_in_client(env["c"], env["db"], boss, other)
    assert b.get("/api/v1/views").json() == []


def test_accounts_labelled_with_names(env) -> None:
    from costlens.db.tenant import set_tenant
    from costlens.models import CloudAccount

    with env["db"].session() as s:
        set_tenant(s, org_id=env["org"])
        s.add(CloudAccount(org_id=env["org"], provider="aws", external_id="1", name="Production"))
    r = explore(env, group_by=["account"], granularity="day")
    assert r["groups"][0]["label"] == "Production (1)" and r["groups"][0]["key"] == ["1"]
    assert r["series"][0]["label"] == "Production (1)"
