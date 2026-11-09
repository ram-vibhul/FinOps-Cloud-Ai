from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from costlens.billing import plans
from costlens.config import DeploymentMode, Settings
from costlens.db import Database
from costlens.db.tenant import set_tenant
from costlens.models import Organization, User
from tests.helpers import make_org, sign_in_client


def org(**settings) -> Organization:
    return Organization(name="Acme", slug="acme", settings=settings, is_demo=False)


def test_catalogue_is_validated() -> None:
    cat = plans.load()
    assert cat.placeholder and set(cat.plans) == {"free", "team", "business", "enterprise"}
    assert cat.plans["enterprise"].limit("members") is None  # null = unlimited
    with pytest.raises(plans.CatalogueError, match="unknown"):
        plans.parse(
            {
                "default_plan": "x",
                "trial_plan": "x",
                "self_hosted_default": "x",
                "plans": [{"id": "x", "name": "X", "features": ["teleport"]}],
            }
        )
    with pytest.raises(plans.CatalogueError, match="trial_plan"):
        plans.parse(
            {
                "default_plan": "x",
                "trial_plan": "gold",
                "self_hosted_default": "x",
                "plans": [{"id": "x", "name": "X"}],
            }
        )


def test_which_plan_applies(settings: Settings) -> None:
    assert plans.plan_for(org(), settings) is plans.UNLIMITED  # local mode
    settings.deployment_mode = DeploymentMode.SAAS
    assert plans.plan_for(org(), settings).id == "free"
    soon = (datetime.now(UTC) + timedelta(days=5)).isoformat()
    assert plans.plan_for(org(trial_ends_at=soon), settings).id == "business"
    assert plans.plan_for(org(trial_ends_at=soon, plan="team"), settings).id == "team"
    demo = org()
    demo.is_demo = True
    assert plans.plan_for(demo, settings) is plans.UNLIMITED
    settings.deployment_mode = DeploymentMode.SELF_HOSTED
    assert plans.plan_for(org(), settings).id == "free"  # free tier without a licence (B.6)


def test_own_catalogue_file(settings: Settings, tmp_path: Path) -> None:
    path = tmp_path / "plans.json"
    path.write_text(
        json.dumps(
            {
                "default_plan": "starter",
                "trial_plan": "starter",
                "self_hosted_default": "starter",
                "plans": [{"id": "starter", "name": "Starter", "limits": {"members": 2}}],
            }
        )
    )
    settings.deployment_mode = DeploymentMode.SAAS
    settings.plans_file = str(path)
    p = plans.plan_for(org(), settings)
    assert (p.name, p.limit("members"), p.limit("clusters")) == ("Starter", 2, None)


@pytest.fixture
def saas(client: TestClient, db: Database):
    from costlens.jobs import load_all_jobs

    load_all_jobs()  # invitations queue an email
    settings = client.app.state.settings  # type: ignore[attr-defined]
    settings.deployment_mode = DeploymentMode.SAAS
    settings.aws_principal_arn = "arn:aws:iam::999999999999:role/costlens-collector"
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    return {"c": sign_in_client(client, db, owner, org_id), "org": org_id, "owner": owner}


def put_on(db: Database, org_id, plan: str) -> None:
    with db.session() as s:
        set_tenant(s, bypass=True)
        o = s.get(Organization, org_id)
        assert o is not None
        o.settings = {**o.settings, "plan": plan}


def test_limits_refuse_to_add_more(saas, db: Database) -> None:
    c = saas["c"]
    out = c.get("/api/v1/org/plan").json()
    assert out["plan"] == {"id": "free", "name": "Free"} and out["placeholder"]
    members = next(x for x in out["limits"] if x["key"] == "members")
    assert members == {"key": "members", "label": "People", "limit": 3, "used": 1}
    for n in (1, 2):
        r = c.post("/api/v1/org/invitations", json={"email": f"p{n}@acme.com", "role": "member"})
        assert r.status_code == 201  # invitations hold a seat
    full = c.post("/api/v1/org/invitations", json={"email": "p3@acme.com", "role": "member"})
    assert full.status_code == 402 and full.json()["code"] == "plan_limit"
    assert "allows 3 people" in full.json()["detail"]
    assert c.post("/api/v1/connections/aws", json={"name": "A"}).status_code == 201
    second = c.post("/api/v1/connections/aws", json={"name": "B"})
    assert second.json()["code"] == "plan_limit"
    assert c.post("/api/v1/clusters", json={"name": "prod"}).status_code == 201
    assert c.post("/api/v1/clusters", json={"name": "dev"}).json()["code"] == "plan_limit"
    bad = c.patch("/api/v1/org/retention", json={"audit_log": 365})
    assert bad.status_code == 402 and "at most 90 days" in bad.json()["detail"]
    assert c.patch("/api/v1/org/retention", json={"audit_log": 90}).status_code == 200
    # A bigger plan lifts the limits; nothing that exists was removed meanwhile.
    put_on(db, saas["org"], "team")
    assert c.post("/api/v1/connections/aws", json={"name": "B"}).status_code == 201
    assert len(c.get("/api/v1/connections").json()) == 2


def test_features_are_gated(saas, db: Database) -> None:
    c = saas["c"]
    r = c.post("/api/v1/org/api-keys", json={"name": "ci"})
    assert r.status_code == 402 and r.json()["code"] == "plan_feature"
    assert "API keys is not included in the Free plan" in r.json()["detail"]
    assert c.post("/api/v1/org/custom-roles", json={"name": "x", "permissions": []}).json()[
        "code"
    ] == ("plan_feature")
    put_on(db, saas["org"], "team")
    assert c.post("/api/v1/org/api-keys", json={"name": "ci"}).status_code == 201
    out = c.get("/api/v1/org/plan").json()
    included = {f["id"] for f in out["features"] if f["included"]}
    assert "api_keys" in included and "sso" not in included and out["source"] == "chosen"


def test_staff_set_plans(saas, db: Database) -> None:
    c = saas["c"]
    path = f"/api/v1/platform/organizations/{saas['org']}/plan"
    assert c.put(path, json={"plan": "team"}).status_code == 403
    with db.session() as s:
        set_tenant(s, bypass=True)
        u = s.get(User, saas["owner"])
        assert u is not None
        u.is_platform_admin = True
    assert c.put(path, json={"plan": "gold"}).json()["code"] == "unknown_plan"
    assert c.put(path, json={"plan": "business"}).json()["plan"] == "business"
    assert c.get("/api/v1/org/plan").json()["plan"]["id"] == "business"
    assert c.put(path, json={"plan": None}).json()["plan"] == "free"
    assert c.get("/api/v1/plans").json()["plans"][0]["id"] == "free"


def test_local_mode_has_everything(client: TestClient, db: Database) -> None:
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    c = sign_in_client(client, db, owner, org_id)
    out = c.get("/api/v1/org/plan").json()
    assert out["plan"]["id"] == "unlimited" and out["source"] == "local"
    assert all(f["included"] for f in out["features"])
    assert all(x["limit"] is None for x in out["limits"])
