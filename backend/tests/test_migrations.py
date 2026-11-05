from __future__ import annotations

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from costlens import models  # noqa: F401
from costlens.cli import alembic_config, migrate
from costlens.db import Base, Database


def test_upgrade_and_downgrade(database_url: str) -> None:
    url = database_url
    migrate(url)
    db = Database(url)
    assert inspect(db.engine).has_table("instance_settings")
    command.downgrade(alembic_config(url), "base")
    assert not inspect(db.engine).has_table("instance_settings")
    db.dispose()


def test_models_match_migrations(database_url: str) -> None:
    """Fails when a model changes without a migration."""
    url = database_url
    migrate(url)
    db = Database(url)
    with db.engine.connect() as conn:
        context = MigrationContext.configure(
            conn,
            opts={
                "include_name": lambda name, type_, _p: not (name or "").startswith("procrastinate")
            },
        )
        diff = compare_metadata(context, Base.metadata)
    db.dispose()
    assert diff == []
