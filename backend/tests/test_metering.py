from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from costlens.billing import metering
from costlens.config import DeploymentMode, Settings
from costlens.core.errors import AppError
from costlens.db import Database
from costlens.db.base import utcnow
from costlens.db.tenant import set_tenant
from costlens.models import Organization
from tests.helpers import make_org, sign_in_client


def tiny_plans(path: Path, api_calls: int | None = 3, ai: int | None = 1) -> str:
    path.write_text(
        json.dumps(
            {
                "default_plan": "tiny",
                "trial_plan": "tiny",
                "self_hosted_default": "tiny",
                "plans": [
                    {
                        "id": "tiny",
                        "name": "Tiny",
                        "features": ["api_keys", "ai"],
                        "limits": {"api_calls_daily": api_calls, "ai_summaries_monthly": ai},
                    }
                ],
            }
        )
    )
    return str(path)


def test_counters_add_up(db: Database) -> None:
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    today = utcnow().date()
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        assert metering.bump(s, org_id, "api_calls") == 1
        assert metering.bump(s, org_id, "api_calls", by=4) == 5
        metering.bump(s, org_id, "api_calls", day=today - timedelta(days=1))
        assert metering.counted(s, "api_calls", today) == 5
        assert metering.counted(s, "api_calls", today - timedelta(days=1)) == 6
    other, _ = make_org(db, "Other", "x@other.com")
    with db.session() as s:
        set_tenant(s, org_id=other)
        assert metering.api_calls_today(s) == 0  # per organisation


def test_api_calls_are_counted_and_limited(
    client: TestClient, db: Database, tmp_path: Path
) -> None:
    settings = client.app.state.settings  # type: ignore[attr-defined]
    settings.deployment_mode = DeploymentMode.SAAS
    settings.plans_file = tiny_plans(tmp_path / "plans.json")
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    c = sign_in_client(client, db, owner, org_id)
    token = c.post("/api/v1/org/api-keys", json={"name": "ci", "scope": "read"}).json()["key"]
    client.cookies.clear()
    headers = {"Authorization": f"Bearer {token}"}
    for _ in range(3):
        assert client.get("/api/v1/org/plan", headers=headers).status_code == 200
    over = client.get("/api/v1/org/plan", headers=headers)
    assert over.status_code == 429 and over.json()["code"] == "plan_limit"
    c = sign_in_client(client, db, owner, org_id)
    used = {x["key"]: x["used"] for x in c.get("/api/v1/org/plan").json()["limits"]}
    assert used["api_calls_daily"] == 3  # the refused call is rolled back with its request
    assert used["ai_summaries_monthly"] == 0


def test_ai_summaries_have_a_monthly_allowance(
    settings: Settings, db: Database, tmp_path: Path
) -> None:
    settings.deployment_mode = DeploymentMode.SAAS
    settings.plans_file = tiny_plans(tmp_path / "plans.json", ai=1)
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        assert metering.use_ai(s, settings) is True
        assert metering.use_ai(s, settings) is False  # used up: plain wording instead
        with pytest.raises(AppError, match="AI summaries a month"):
            metering.use_ai(s, settings, strict=True)
        assert metering.ai_summaries_this_month(s) == 1


def test_snapshots_and_history(client: TestClient, db: Database) -> None:
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    yesterday = utcnow().date() - timedelta(days=1)
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        metering.bump(s, org_id, "api_calls", by=7, day=yesterday)
    with db.session() as s:
        set_tenant(s, bypass=True)
        assert metering.snapshot_all(s) >= 1
        assert metering.snapshot_all(s) >= 1  # re-running rewrites, no duplicates
    c = sign_in_client(client, db, owner, org_id)
    out = c.get("/api/v1/org/usage").json()
    assert out["days"] == [
        {
            "day": yesterday.isoformat(),
            "members": 1,
            "connections": 0,
            "clusters": 0,
            "cloud_spend_monthly_usd": 0.0,
            "api_calls": 7,
            "ai_summaries": 0,
        }
    ]
    assert out["now"]["members"] == 1


def test_usage_is_a_retention_kind() -> None:
    from costlens.privacy.retention import policies

    kinds = {p.kind for p in policies()}
    assert {"usage", "usage_counters", "notifications"} <= kinds


def test_demo_is_not_snapshotted(db: Database) -> None:
    with db.session() as s:
        set_tenant(s, bypass=True)
        demo = Organization(name="Demo", slug="demo-z", is_demo=True)
        s.add(demo)
        s.flush()
        metering.snapshot_all(s, day=date(2026, 1, 1))
        set_tenant(s, org_id=demo.id)
        assert metering.history(s, 3650) == []
