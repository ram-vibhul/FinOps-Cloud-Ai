"""Shared fixtures.

Tests use SQLite by default. Set COSTLENS_TEST_DATABASE_URL to a PostgreSQL URL
(as CI does) to run the same suite against PostgreSQL; the schema is reset for
every test. On PostgreSQL that URL is the admin (owner) connection used for
resets and migrations, and the application runs as a separate non-privileged
role so row-level security is exercised by every test.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import make_url

from costlens.cli import migrate
from costlens.config import Settings
from costlens.db import Database
from costlens.db.session import build_engine
from costlens.jobs import InProcessRunner
from costlens.main import create_app

POSTGRES_URL = os.environ.get("COSTLENS_TEST_DATABASE_URL")
APP_ROLE = "costlens_app_test"
APP_PASSWORD = "app-test-password"


def app_url(admin_url: str) -> str:
    url = make_url(admin_url).set(username=APP_ROLE, password=APP_PASSWORD)
    return url.render_as_string(hide_password=False)


def reset_postgres(url: str) -> None:
    engine = build_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        exists = conn.execute(
            text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": APP_ROLE}
        ).scalar()
        if not exists:
            conn.execute(text(f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PASSWORD}'"))
    engine.dispose()


@pytest.fixture
def admin_database_url(tmp_path) -> str:
    """Owner connection (migrations, resets). Same as database_url on SQLite."""
    if POSTGRES_URL:
        reset_postgres(POSTGRES_URL)
        return POSTGRES_URL
    return f"sqlite:///{tmp_path}/test.db"


@pytest.fixture
def database_url(admin_database_url: str) -> str:
    """Runtime connection: the non-privileged app role on PostgreSQL."""
    return app_url(admin_database_url) if POSTGRES_URL else admin_database_url


def migrate_for_tests(admin_url: str) -> None:
    migrate(admin_url, app_role=APP_ROLE if POSTGRES_URL else None)


@pytest.fixture
def settings(database_url: str) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url=database_url,
        jobs_in_process=True,
    )


@pytest.fixture
def db(settings: Settings, admin_database_url: str) -> Iterator[Database]:
    migrate_for_tests(admin_database_url)
    database = Database(settings.database_url)
    yield database
    database.dispose()


@pytest.fixture
def client(settings: Settings, db: Database) -> Iterator[TestClient]:
    app = create_app(settings, db=db, runner=InProcessRunner())
    with TestClient(app) as test_client:
        yield test_client
