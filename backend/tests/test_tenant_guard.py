"""Application-level tenant guard (runs on SQLite and PostgreSQL)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, aliased

from ccradar.accounts import service as acc
from ccradar.accounts.roles import Role
from ccradar.db import Database
from ccradar.db.tenant import SKIP_GUARD_OPTION, TenantIsolationError, set_tenant
from ccradar.models import Membership, Organization, User
from tests.conftest import POSTGRES_URL


@pytest.fixture
def ids(db: Database) -> dict[str, uuid.UUID]:
    with db.session() as s:
        alice = acc.sign_in_with_identity(
            s, provider="t", subject="a", email="alice@a.com", email_verified=True
        )
        bob = acc.sign_in_with_identity(
            s, provider="t", subject="b", email="bob@b.com", email_verified=True
        )
        carol = acc.sign_in_with_identity(
            s, provider="t", subject="c", email="carol@c.com", email_verified=True
        )
        a = acc.create_organization(s, name="A", owner=alice)
        acc.add_member(s, org_id=a.id, actor_id=alice.id, user=carol, role=Role.MEMBER)
        b = acc.create_organization(s, name="B", owner=bob)
        acc.add_member(s, org_id=b.id, actor_id=bob.id, user=carol, role=Role.VIEWER)
        return {"a": a.id, "b": b.id, "alice": alice.id, "bob": bob.id, "carol": carol.id}


@pytest.fixture
def s(db: Database) -> Iterator[Session]:
    with db.session() as session:
        yield session


def test_queries_are_scoped_to_current_org(s: Session, ids: dict) -> None:
    set_tenant(s, org_id=ids["a"])
    assert set(s.scalars(select(Membership.org_id))) == {ids["a"]}
    assert s.scalar(select(func.count()).select_from(Membership)) == 2
    # Even an explicit filter for org B returns nothing.
    assert s.scalars(select(Membership).where(Membership.org_id == ids["b"])).all() == []


def test_scoping_applies_to_joins_and_aliases(s: Session, ids: dict) -> None:
    set_tenant(s, org_id=ids["a"])
    rows = s.execute(select(User.email).join(Membership, Membership.user_id == User.id)).all()
    assert {r.email for r in rows} == {"alice@a.com", "carol@c.com"}
    m = aliased(Membership)
    assert set(s.scalars(select(m.org_id))) == {ids["a"]}


def test_missing_context_raises(s: Session, ids: dict) -> None:
    with pytest.raises(TenantIsolationError, match="without an organisation"):
        s.scalars(select(Membership)).all()
    # Global models need no context.
    assert len(s.scalars(select(Organization)).all()) == 2


def test_user_context_reads_own_rows_only(s: Session, ids: dict) -> None:
    set_tenant(s, user_id=ids["carol"])
    rows = s.scalars(select(Membership)).all()
    assert {m.org_id for m in rows} == {ids["a"], ids["b"]}
    assert {m.user_id for m in rows} == {ids["carol"]}


def test_org_and_user_context_combined(s: Session, ids: dict) -> None:
    set_tenant(s, org_id=ids["b"], user_id=ids["alice"])
    rows = s.scalars(select(Membership)).all()
    # All of org B, plus alice's own membership in A.
    assert {(m.org_id, m.user_id) for m in rows} == {
        (ids["b"], ids["bob"]),
        (ids["b"], ids["carol"]),
        (ids["a"], ids["alice"]),
    }


def test_bulk_update_and_delete_are_scoped(s: Session, ids: dict) -> None:
    set_tenant(s, org_id=ids["a"])
    s.execute(
        update(Membership).values(role=Role.VIEWER),
        execution_options={"synchronize_session": False},
    )
    s.execute(
        delete(Membership).where(Membership.user_id == ids["bob"]),
        execution_options={"synchronize_session": False},
    )
    set_tenant(s, org_id=ids["b"])
    roles = {m.user_id: m.role for m in s.scalars(select(Membership))}
    assert roles == {ids["bob"]: Role.OWNER, ids["carol"]: Role.VIEWER}


def test_insert_fills_org_from_context(s: Session, ids: dict) -> None:
    set_tenant(s, org_id=ids["b"])
    m = Membership(user_id=ids["alice"], role=Role.VIEWER)
    s.add(m)
    s.flush()
    assert m.org_id == ids["b"]


def test_insert_into_other_org_raises(s: Session, ids: dict) -> None:
    set_tenant(s, org_id=ids["a"])
    s.add(Membership(org_id=ids["b"], user_id=ids["alice"], role=Role.OWNER))
    with pytest.raises(TenantIsolationError, match="another organisation"):
        s.flush()
    s.rollback()


def test_insert_without_context_raises(s: Session, ids: dict) -> None:
    s.add(Membership(org_id=ids["a"], user_id=ids["bob"], role=Role.VIEWER))
    with pytest.raises(TenantIsolationError):
        s.flush()
    s.rollback()


def test_moving_row_between_orgs_raises(s: Session, ids: dict) -> None:
    set_tenant(s, org_id=ids["a"])
    m = s.scalars(select(Membership).where(Membership.user_id == ids["carol"])).one()
    m.org_id = ids["b"]
    with pytest.raises(TenantIsolationError, match="between organisations"):
        s.flush()
    s.rollback()


def test_changing_row_loaded_under_other_context_raises(s: Session, ids: dict) -> None:
    set_tenant(s, org_id=ids["a"])
    m = s.scalars(select(Membership).where(Membership.user_id == ids["carol"])).one()
    set_tenant(s, org_id=ids["b"])
    m.role = Role.ADMIN
    with pytest.raises(TenantIsolationError, match="another organisation"):
        s.flush()
    s.rollback()


def test_deleting_other_org_row_raises(s: Session, ids: dict) -> None:
    set_tenant(s, user_id=ids["carol"])
    m = s.scalars(select(Membership).where(Membership.org_id == ids["b"])).one()
    set_tenant(s, org_id=ids["a"], user_id=ids["carol"])
    s.delete(m)
    with pytest.raises(TenantIsolationError):
        s.flush()
    s.rollback()


def test_bypass_and_explicit_skip(s: Session, ids: dict) -> None:
    set_tenant(s, bypass=True)
    assert len(s.scalars(select(Membership)).all()) == 4
    set_tenant(s)
    rows = s.scalars(select(Membership), execution_options={SKIP_GUARD_OPTION: True}).all()
    # Skipping the app guard leaves the database layer: on PostgreSQL RLS still
    # hides everything (defence in depth); SQLite has no second layer.
    assert len(rows) == (0 if POSTGRES_URL else 4)


def test_services_respect_scope(s: Session, ids: dict) -> None:
    set_tenant(s, org_id=ids["a"], user_id=ids["alice"])
    assert acc.get_membership(s, ids["b"], ids["bob"]) is None
    with pytest.raises(Exception, match="not found"):
        acc.change_role(
            s, org_id=ids["b"], actor_id=ids["alice"], user_id=ids["bob"], role=Role.VIEWER
        )
