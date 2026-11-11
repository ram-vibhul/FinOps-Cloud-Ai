from __future__ import annotations

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from ccradar import models  # noqa: F401
from ccradar.cli import alembic_config, migrate
from ccradar.db import Base, Database
from ccradar.db.schema_filter import include_name


def test_upgrade_and_downgrade(admin_database_url: str) -> None:
    url = admin_database_url
    migrate(url)
    db = Database(url)
    assert inspect(db.engine).has_table("instance_settings")
    command.downgrade(alembic_config(url), "base")
    assert not inspect(db.engine).has_table("instance_settings")
    db.dispose()


def test_models_match_migrations(admin_database_url: str) -> None:
    """Fails when a model changes without a migration."""
    url = admin_database_url
    migrate(url)
    db = Database(url)
    with db.engine.connect() as conn:
        context = MigrationContext.configure(conn, opts={"include_name": include_name})
        diff = compare_metadata(context, Base.metadata)
    db.dispose()
    assert diff == []
