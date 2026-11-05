"""Shared fixtures.

Tests use SQLite by default. Set COSTLENS_TEST_DATABASE_URL to a PostgreSQL URL
(as CI does) to run the same suite against PostgreSQL; the schema is reset for
every test.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from costlens.cli import migrate
from costlens.config import Settings
from costlens.db import Database
from costlens.db.session import build_engine
from costlens.jobs import InProcessRunner
from costlens.main import create_app

POSTGRES_URL = os.environ.get("COSTLENS_TEST_DATABASE_URL")


def reset_postgres(url: str) -> None:
    engine = build_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()


@pytest.fixture
def database_url(tmp_path) -> str:
    if POSTGRES_URL:
        reset_postgres(POSTGRES_URL)
        return POSTGRES_URL
    return f"sqlite:///{tmp_path}/test.db"


@pytest.fixture
def settings(database_url: str) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url=database_url,
        jobs_in_process=True,
    )


@pytest.fixture
def db(settings: Settings) -> Iterator[Database]:
    migrate(settings.database_url)
    database = Database(settings.database_url)
    yield database
    database.dispose()


@pytest.fixture
def client(settings: Settings, db: Database) -> Iterator[TestClient]:
    app = create_app(settings, db=db, runner=InProcessRunner())
    with TestClient(app) as test_client:
        yield test_client
