from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from costlens.accounts.roles import Role
from costlens.db import Database
from costlens.db.base import utcnow
from costlens.db.tenant import set_tenant
from costlens.demo import seed as demo
from costlens.models import Invitation, Membership
from tests.helpers import make_user, sign_in_client


@pytest.fixture
def seeders() -> Iterator[list[tuple]]:
    demo.load_all_seeders()  # snapshot includes the real seeders, so restoring keeps them
    saved = list(demo._SEEDERS)
    calls: list[tuple] = []

    @demo.demo_seeder("test.second", order=20)
    def second(db, ctx) -> None:
        calls.append(("second", ctx.today, ctx.rng.random()))

    @demo.demo_seeder("test.first", order=10)
    def first(db, ctx) -> None:
        calls.append(("first", ctx.today, ctx.rng.random()))
        db.add(
            Invitation(
                org_id=ctx.org_id,
                email="sample@demo.example",
                role=Role.VIEWER,
                token_hash=f"demo-{ctx.rng.random()}",
                expires_at=utcnow() + timedelta(days=1),
            )
        )

    yield calls
    demo._SEEDERS[:] = saved


def test_seeders_run_in_order_deterministically(db: Database, seeders) -> None:
    today = date(2026, 11, 5)
    with db.session() as s:
        demo.ensure_demo(s, today=today)
    first_run = list(seeders)
    assert [c[0] for c in first_run] == ["first", "second"]
    assert all(c[1] == today for c in first_run)
    with db.session() as s:
        demo.reset_demo(s, today=today)
    assert seeders[2:] == first_run  # same random values after reset


def test_duplicate_seeder_name_rejected(seeders) -> None:
    with pytest.raises(ValueError):
        demo.demo_seeder("test.first")(lambda db, ctx: None)


def test_reset_replaces_data_but_keeps_members(db: Database, seeders) -> None:
    uid = make_user(db, "visitor@x.com")
    with db.session() as s:
        org = demo.join_demo(s, uid)
        org_id = org.id
    with db.session() as s:
        set_tenant(s, org_id=org_id, bypass=True)
        s.add(
            Invitation(
                org_id=org_id,
                email="extra@x.com",
                role=Role.VIEWER,
                token_hash="extra",
                expires_at=utcnow() + timedelta(days=1),
            )
        )
    with db.session() as s:
        demo.reset_demo(s)
    with db.session() as s:
        set_tenant(s, bypass=True)
        emails = set(s.scalars(select(Invitation.email).where(Invitation.org_id == org_id)))
        assert emails == {"sample@demo.example"}
        assert s.scalar(select(Membership).where(Membership.user_id == uid)) is not None


def test_join_demo_api(client: TestClient, db: Database) -> None:
    uid = make_user(db, "visitor@x.com")
    c = sign_in_client(client, db, uid)
    r = c.post("/api/v1/demo/join")
    assert r.status_code == 200
    again = c.post("/api/v1/demo/join")
    assert again.json() == r.json()  # idempotent, same org
    me = c.get("/api/v1/auth/me").json()
    assert me["role"] == "viewer"
    assert me["organizations"] == [
        {
            "id": r.json()["organization_id"],
            "name": "Demo Company",
            "slug": "demo",
            "role": "viewer",
            "is_demo": True,
            "is_partner": False,
            "partner_id": None,
        }
    ]


def test_demo_is_read_only_even_for_admins(client: TestClient, db: Database) -> None:
    uid = make_user(db, "staff@x.com")
    with db.session() as s:
        org = demo.ensure_demo(s)
        set_tenant(s, org_id=org.id)
        s.add(Membership(org_id=org.id, user_id=uid, role=Role.ADMIN))
        org_id = org.id
    c = sign_in_client(client, db, uid, org_id)
    r = c.patch("/api/v1/org", json={"name": "Hacked"})
    assert r.status_code == 403 and r.json()["code"] == "demo_read_only"
    spec = {
        "kind": "oidc",
        "display_name": "X",
        "issuer": "https://sso.example.com",
        "client_id": "c",
        "client_secret": "s",
    }
    assert c.post("/api/v1/org/sso", json=spec).json()["code"] == "demo_read_only"
    assert c.get("/api/v1/org").status_code == 200  # reading is fine
    assert c.delete(f"/api/v1/org/members/{uid}").status_code == 204  # leaving is fine


def test_demo_can_be_disabled(client: TestClient, db: Database) -> None:
    client.app.state.settings.demo_enabled = False  # type: ignore[attr-defined]
    c = sign_in_client(client, db, make_user(db, "v@x.com"))
    assert c.post("/api/v1/demo/join").status_code == 404


def test_customers_cannot_take_reserved_slugs(db: Database) -> None:
    from costlens.models import Organization
    from tests.helpers import make_org

    org_id, _ = make_org(db, "Demo", "someone@x.com")
    with db.session() as s:
        set_tenant(s, bypass=True)
        org = s.get(Organization, org_id)
        assert org is not None and org.slug.startswith("demo-")
        assert demo.ensure_demo(s).slug == "demo"


def test_all_feature_seeders_register() -> None:
    demo.load_all_seeders()
    assert {"costs.aws", "inventory.aws", "changes.events", "costs.anomalies"} <= set(
        demo.seeders()
    )


def test_an_empty_demo_is_seeded_again(db: Database) -> None:
    from sqlalchemy import delete, func, select

    from costlens.demo.seed import ensure_demo, load_all_seeders
    from costlens.models import CostDaily

    load_all_seeders()
    with db.session() as s:
        org_id = ensure_demo(s).id
    with db.session() as s:
        set_tenant(s, bypass=True)
        s.execute(delete(CostDaily).where(CostDaily.org_id == org_id))
    with db.session() as s:
        assert ensure_demo(s).id == org_id
        set_tenant(s, bypass=True)
        assert (
            s.scalar(select(func.count()).select_from(CostDaily).where(CostDaily.org_id == org_id))
            or 0
        ) > 0
