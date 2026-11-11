from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ccradar.accounts.roles import Role
from ccradar.billing import subscriptions
from ccradar.config import DeploymentMode
from ccradar.db import Database
from ccradar.db.tenant import set_tenant
from ccradar.models import InboxItem, Organization, Subscription, User
from tests.helpers import add_to_org, make_org, sign_in_client


def roll(db: Database, days: int) -> dict[str, int]:
    with db.session() as s:
        set_tenant(s, bypass=True)
        return subscriptions.roll(s, now=datetime.now(UTC) + timedelta(days=days))


@pytest.fixture
def saas(client: TestClient, db: Database):
    client.app.state.settings.deployment_mode = DeploymentMode.SAAS  # type: ignore[attr-defined]
    seed_org, owner = make_org(db, "Seed", "o@acme.com")
    c = sign_in_client(client, db, owner, seed_org)
    org_id = uuid.UUID(c.post("/api/v1/organizations", json={"name": "Acme"}).json()["id"])
    return {"c": sign_in_client(client, db, owner, org_id), "org": org_id, "owner": owner}


def staff(db: Database, user_id) -> None:
    with db.session() as s:
        set_tenant(s, bypass=True)
        u = s.get(User, user_id)
        assert u is not None
        u.is_platform_admin = True


def test_months_are_added_safely() -> None:
    jan31 = datetime(2026, 1, 31, tzinfo=UTC)
    assert subscriptions.add_months(jan31, 1) == datetime(2026, 2, 28, tzinfo=UTC)
    assert subscriptions.period_end(jan31, "annual") == datetime(2027, 1, 31, tzinfo=UTC)


def test_trial_then_free_plan(saas, db: Database) -> None:
    c = saas["c"]
    sub = c.get("/api/v1/org/subscription").json()
    assert sub["status"] == "trialing" and sub["plan"]["id"] == "business"
    assert not sub["payments_available"]
    upgrade = c.post("/api/v1/org/subscription/change", json={"plan": "team"})
    assert upgrade.status_code == 409 and upgrade.json()["code"] == "payment_unavailable"
    assert roll(db, 13) == {
        "ended": 0,
        "downgraded": 0,
        "renewed": 0,
        "reminded": 0,
        "downgraded_unpaid": 0,
    }
    assert roll(db, 15)["ended"] == 1
    after = c.get("/api/v1/org/subscription").json()
    assert after["status"] == "ended" and after["plan"]["id"] == "free"
    assert c.get("/api/v1/org/trial").json()["status"] == "ended"
    with db.session() as s:
        set_tenant(s, bypass=True)
        titles = [i.title for i in s.scalars(select(InboxItem).where(InboxItem.event == "billing"))]
    assert titles == ["Your free trial has ended"]


def test_free_plan_during_trial_ends_it_now(saas) -> None:
    out = saas["c"].post("/api/v1/org/subscription/change", json={"plan": "free"}).json()
    assert out["status"] == "changed" and out["subscription"]["status"] == "ended"
    assert out["subscription"]["plan"]["id"] == "free"


def test_paid_plan_downgrade_cancel_resume(saas, db: Database) -> None:
    c = saas["c"]
    staff(db, saas["owner"])
    path = f"/api/v1/platform/organizations/{saas['org']}/plan"
    assert c.put(path, json={"plan": "business", "interval": "monthly"}).json()["plan"] == (
        "business"
    )
    sub = c.get("/api/v1/org/subscription").json()
    assert (sub["status"], sub["provider"], sub["interval"]) == ("active", "manual", "monthly")
    # Downgrade: scheduled for the end of the period, nothing changes now.
    out = c.post("/api/v1/org/subscription/change", json={"plan": "team"}).json()
    assert out["status"] == "scheduled"
    sub = out["subscription"]
    assert sub["plan"]["id"] == "business" and sub["pending_plan"]["id"] == "team"
    # Choosing the current plan again drops the scheduled downgrade.
    assert c.post("/api/v1/org/subscription/change", json={"plan": "business"}).json()[
        "status"
    ] == ("unchanged")
    c.post("/api/v1/org/subscription/change", json={"plan": "team"})
    assert roll(db, 32)["downgraded"] == 1
    assert c.get("/api/v1/org/plan").json()["plan"]["id"] == "team"
    # Invoiced subscriptions renew until staff end them.
    assert roll(db, 63)["renewed"] == 1
    # Cancel keeps the plan to the end of the period; resume undoes it.
    out = c.post("/api/v1/org/subscription/cancel").json()
    assert out["status"] == "cancelling" and out["subscription"]["cancel_at_period_end"]
    assert not c.post("/api/v1/org/subscription/resume").json()["cancel_at_period_end"]
    c.post("/api/v1/org/subscription/cancel")
    assert c.get("/api/v1/org/plan").json()["plan"]["id"] == "team"
    assert roll(db, 100)["ended"] == 1
    assert c.get("/api/v1/org/plan").json()["plan"]["id"] == "free"
    assert c.post("/api/v1/org/subscription/cancel").json()["code"] == "ended"


def test_only_owners_change_billing(saas, db: Database, client: TestClient) -> None:
    admin = add_to_org(db, saas["org"], "a@acme.com", Role.ADMIN)
    a = sign_in_client(client, db, admin, saas["org"])
    assert a.get("/api/v1/org/subscription").status_code == 200
    assert a.post("/api/v1/org/subscription/cancel").status_code == 403


def test_no_subscription_in_local_mode(client: TestClient, db: Database) -> None:
    org_id, owner = make_org(db, "Seed", "o@acme.com")
    c = sign_in_client(client, db, owner, org_id)
    org = c.post("/api/v1/organizations", json={"name": "Local"}).json()
    c = sign_in_client(client, db, owner, uuid.UUID(org["id"]))
    assert c.get("/api/v1/org/subscription").json()["status"] == "none"
    with db.session() as s:
        set_tenant(s, bypass=True)
        assert s.scalars(select(Subscription)).all() == []
        assert s.get(Organization, uuid.UUID(org["id"])) is not None
