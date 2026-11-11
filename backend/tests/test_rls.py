"""Tenant isolation enforced by PostgreSQL row-level security.

These tests only run against PostgreSQL (CCRADAR_TEST_DATABASE_URL). The
application connection is the non-privileged app role, so RLS really applies.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.orm import Session

from ccradar.accounts import service as acc
from ccradar.accounts.roles import Role
from ccradar.config import DeploymentMode, Settings
from ccradar.db import Database
from ccradar.db.rls import enable_tenant_rls_sql, ident, is_privileged_role, rls_status
from ccradar.db.tenant import current_tenant, set_tenant, tenant_tables
from ccradar.jobs import InProcessRunner
from ccradar.main import create_app
from tests.conftest import POSTGRES_URL, TEST_ENCRYPTION_KEY

pg_only = pytest.mark.skipif(not POSTGRES_URL, reason="needs PostgreSQL")


@pytest.fixture
def two_orgs(db: Database) -> dict[str, uuid.UUID]:
    """Org A (alice owner, carol member) and org B (bob owner, carol viewer)."""
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
        org_a = acc.create_organization(s, name="A", owner=alice)
        acc.add_member(s, org_id=org_a.id, actor_id=alice.id, user=carol, role=Role.MEMBER)
        org_b = acc.create_organization(s, name="B", owner=bob)
        acc.add_member(s, org_id=org_b.id, actor_id=bob.id, user=carol, role=Role.VIEWER)
        return {
            "a": org_a.id,
            "b": org_b.id,
            "alice": alice.id,
            "bob": bob.id,
            "carol": carol.id,
        }


@pytest.fixture
def s(db: Database) -> Iterator[Session]:
    with db.session() as session:
        yield session


def org_ids(s: Session) -> set[uuid.UUID]:
    """Raw SQL on purpose: tests the database policy, not the ORM guard (1.3)."""
    return {uuid.UUID(str(r)) for r in s.scalars(text("SELECT org_id FROM memberships"))}


# --- coverage guard (runs on SQLite too: checks the migration registry) -----


def test_every_tenant_table_has_rls(admin_database_url: str, db: Database) -> None:
    if not POSTGRES_URL:
        pytest.skip("needs PostgreSQL")
    from ccradar.db.session import build_engine

    engine = build_engine(admin_database_url)
    with engine.connect() as conn:
        status = rls_status(conn)
    engine.dispose()
    tables = tenant_tables()
    assert tables, "no tenant tables registered"
    missing = {t: status.get(t) for t in tables if status.get(t) != (True, True, True)}
    assert missing == {}, f"tenant tables without enforced RLS: {missing}"


def test_identifier_validation() -> None:
    assert ident("memberships") == '"memberships"'
    with pytest.raises(ValueError):
        ident('x"; DROP TABLE users; --')
    assert any("FORCE ROW LEVEL SECURITY" in q for q in enable_tenant_rls_sql("t"))


def test_set_tenant_is_harmless_on_sqlite(s: Session) -> None:
    ctx = set_tenant(s, org_id=uuid.uuid4())
    assert current_tenant(s) == ctx
    s.execute(text("SELECT 1"))


# --- isolation --------------------------------------------------------------


@pg_only
def test_app_role_is_not_privileged(db: Database) -> None:
    with db.engine.connect() as conn:
        assert is_privileged_role(conn) is False


@pg_only
def test_no_context_sees_nothing(s: Session, two_orgs: dict) -> None:
    assert org_ids(s) == set()
    # Global tables are not tenant-scoped.
    assert s.scalar(text("SELECT count(*) FROM users")) == 3
    assert s.scalar(text("SELECT count(*) FROM organizations")) == 2


@pg_only
def test_org_context_sees_only_that_org(s: Session, two_orgs: dict) -> None:
    set_tenant(s, org_id=two_orgs["a"])
    assert org_ids(s) == {two_orgs["a"]}
    set_tenant(s, org_id=two_orgs["b"])
    assert org_ids(s) == {two_orgs["b"]}


@pg_only
def test_user_context_reads_own_memberships_across_orgs(s: Session, two_orgs: dict) -> None:
    set_tenant(s, user_id=two_orgs["carol"])
    rows = s.execute(text("SELECT org_id, user_id FROM memberships")).all()
    assert {uuid.UUID(str(r.org_id)) for r in rows} == {two_orgs["a"], two_orgs["b"]}
    assert {uuid.UUID(str(r.user_id)) for r in rows} == {two_orgs["carol"]}
    assert len(acc.user_organizations(s, two_orgs["carol"])) == 2


@pg_only
def test_cannot_insert_into_another_org(s: Session, two_orgs: dict) -> None:
    set_tenant(s, org_id=two_orgs["a"], user_id=two_orgs["alice"])
    with pytest.raises((ProgrammingError, DBAPIError), match="row-level security"):
        s.execute(
            text(
                "INSERT INTO memberships (id, org_id, user_id, role, created_at, updated_at) "
                "VALUES (:id, :org, :user, 'owner', now(), now())"
            ),
            {"id": uuid.uuid4(), "org": two_orgs["b"], "user": two_orgs["alice"]},
        )
    s.rollback()


@pg_only
def test_cannot_move_row_to_another_org(s: Session, two_orgs: dict) -> None:
    set_tenant(s, org_id=two_orgs["a"])
    with pytest.raises((ProgrammingError, DBAPIError), match="row-level security"):
        s.execute(
            text("UPDATE memberships SET org_id = :b WHERE org_id = :a"),
            {"a": two_orgs["a"], "b": two_orgs["b"]},
        )
    s.rollback()


@pg_only
def test_cannot_update_or_delete_other_org_rows(s: Session, two_orgs: dict) -> None:
    set_tenant(s, org_id=two_orgs["a"])
    updated = s.execute(
        text("UPDATE memberships SET role = 'viewer' WHERE org_id = :b"), {"b": two_orgs["b"]}
    )
    deleted = s.execute(text("DELETE FROM memberships WHERE org_id = :b"), {"b": two_orgs["b"]})
    assert updated.rowcount == 0  # type: ignore[attr-defined]
    assert deleted.rowcount == 0  # type: ignore[attr-defined]


@pg_only
def test_service_cannot_reach_other_org(s: Session, two_orgs: dict) -> None:
    """Even a service call naming org B finds nothing while scoped to org A."""
    set_tenant(s, org_id=two_orgs["a"], user_id=two_orgs["alice"])
    assert acc.get_membership(s, two_orgs["b"], two_orgs["bob"]) is None


@pg_only
def test_bypass_sees_everything(s: Session, two_orgs: dict) -> None:
    set_tenant(s, bypass=True)
    assert org_ids(s) == {two_orgs["a"], two_orgs["b"]}


@pg_only
def test_context_survives_commit_and_is_transaction_local(db: Database, two_orgs: dict) -> None:
    with db.session() as s:
        set_tenant(s, org_id=two_orgs["a"])
        assert org_ids(s) == {two_orgs["a"]}
        s.commit()
        assert org_ids(s) == {two_orgs["a"]}  # re-applied on the next transaction
    with db.engine.connect() as conn:  # pooled connection must not leak the setting
        assert conn.execute(text("SELECT count(*) FROM memberships")).scalar() == 0
        assert conn.execute(
            text("SELECT coalesce(current_setting('ccradar.org_id', true), '')")
        ).scalar() in ("", None)


@pg_only
def test_app_role_cannot_disable_rls_or_rewrite_migrations(db: Database) -> None:
    with db.engine.connect() as conn:
        with pytest.raises((ProgrammingError, DBAPIError)):
            conn.execute(text("ALTER TABLE memberships DISABLE ROW LEVEL SECURITY"))
        conn.rollback()
        with pytest.raises((ProgrammingError, DBAPIError)):
            conn.execute(text("DELETE FROM alembic_version"))
        conn.rollback()


# --- startup guard --------------------------------------------------------


def _prod_settings(url: str) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        deployment_mode=DeploymentMode.SELF_HOSTED,
        database_url=url,
        secret_key="s",  # type: ignore[arg-type]
        encryption_keys=TEST_ENCRYPTION_KEY,  # type: ignore[arg-type]
        jobs_in_process=True,
    )


@pg_only
def test_startup_refuses_privileged_role(admin_database_url: str, db: Database) -> None:
    settings = _prod_settings(admin_database_url)
    admin_db = Database(admin_database_url)
    app = create_app(settings, db=admin_db, runner=InProcessRunner())
    with pytest.raises(RuntimeError, match="BYPASSRLS"), TestClient(app):
        pass
    settings.allow_privileged_db_role = True
    with TestClient(create_app(settings, db=admin_db, runner=InProcessRunner())) as c:
        assert c.get("/healthz").status_code == 200


@pg_only
def test_startup_accepts_app_role(database_url: str, db: Database) -> None:
    app = create_app(_prod_settings(database_url), db=db, runner=InProcessRunner())
    with TestClient(app) as c:
        assert c.get("/readyz").json() == {"status": "ok"}
