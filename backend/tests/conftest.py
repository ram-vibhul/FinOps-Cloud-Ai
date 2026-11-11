"""Shared fixtures.

Tests use SQLite by default. Set CCRADAR_TEST_DATABASE_URL to a PostgreSQL URL
(as CI does) to run the same suite against PostgreSQL; the schema is reset for
every test. On PostgreSQL that URL is the admin (owner) connection used for
resets and migrations, and the application runs as a separate non-privileged
role so row-level security is exercised by every test.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import make_url

from ccradar.cli import migrate
from ccradar.config import Settings
from ccradar.core.crypto import SecretBox, configure_encryption
from ccradar.db import Database
from ccradar.db.session import build_engine
from ccradar.jobs import InProcessRunner
from ccradar.main import create_app

POSTGRES_URL = os.environ.get("CCRADAR_TEST_DATABASE_URL")
APP_ROLE = "ccradar_app_test"
TEST_ENCRYPTION_KEY = Fernet.generate_key().decode()
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


def _head_revision() -> str:
    from alembic.script import ScriptDirectory

    from ccradar.cli import alembic_config

    return ScriptDirectory.from_config(alembic_config("sqlite://")).get_current_head() or ""


def prepare_postgres(url: str) -> None:
    """Migrate once per run; between tests only empty the tables (much faster)."""
    engine = build_engine(url)
    try:
        with engine.begin() as conn:
            try:
                current = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
            except Exception:
                current = None
        if current == _head_revision():
            with engine.begin() as conn:
                tables = (
                    conn.execute(
                        text(
                            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                            "AND tablename <> 'alembic_version' "
                            "AND tablename NOT LIKE 'cost_line_items_%'"
                        )
                    )
                    .scalars()
                    .all()
                )
                if tables:
                    names = ", ".join(f'"{t}"' for t in tables)
                    conn.execute(text(f"TRUNCATE {names} CASCADE"))
            return
    finally:
        engine.dispose()
    reset_postgres(url)
    migrate(url, app_role=APP_ROLE, partition_months=(1, 1))


@pytest.fixture
def admin_database_url(tmp_path) -> str:
    """Owner connection (migrations, resets). Same as database_url on SQLite."""
    if POSTGRES_URL:
        prepare_postgres(POSTGRES_URL)
        return POSTGRES_URL
    return f"sqlite:///{tmp_path}/test.db"


@pytest.fixture
def database_url(admin_database_url: str) -> str:
    """Runtime connection: the non-privileged app role on PostgreSQL."""
    return app_url(admin_database_url) if POSTGRES_URL else admin_database_url


def migrate_for_tests(admin_url: str) -> None:
    """SQLite: migrate the fresh file. PostgreSQL: already prepared by the fixture."""
    if POSTGRES_URL:
        return
    migrate(admin_url, partition_months=(1, 1))


@pytest.fixture
def settings(database_url: str, tmp_path) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url=database_url,
        jobs_in_process=True,
        encryption_keys=TEST_ENCRYPTION_KEY,  # type: ignore[arg-type]
        data_dir=str(tmp_path),
    )


@pytest.fixture
def db(settings: Settings, admin_database_url: str) -> Iterator[Database]:
    configure_encryption(SecretBox([TEST_ENCRYPTION_KEY]))
    migrate_for_tests(admin_database_url)
    database = Database(settings.database_url)
    yield database
    database.dispose()


@pytest.fixture
def client(settings: Settings, db: Database) -> Iterator[TestClient]:
    app = create_app(
        settings,
        db=db,
        runner=InProcessRunner(),
        resolver=public_resolver,
        http_client=offline_http_client(),
    )
    with TestClient(app) as test_client:
        yield test_client


def public_resolver(host: str) -> list[str]:
    """Fake DNS: every name is public unless it says otherwise (no network in tests)."""
    if host.endswith(".internal"):
        return ["10.0.0.5"]
    return ["93.184.216.34"]


def offline_http_client() -> httpx.Client:
    """No real network in tests: every outbound request fails like an unreachable host."""

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"network disabled in tests: {request.url}", request=request)

    return httpx.Client(transport=httpx.MockTransport(refuse))


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Belt and braces: any real httpx transport fails loudly in every test."""

    def refuse(self, request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"network disabled in tests: {request.url}", request=request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
