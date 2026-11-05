from __future__ import annotations

from alembic import context
from sqlalchemy import Connection

from costlens import models  # noqa: F401  (register models)
from costlens.db.base import Base
from costlens.db.session import build_engine

config = context.config
target_metadata = Base.metadata


def run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_offline()
else:
    existing = config.attributes.get("connection")
    if existing is not None:
        run_migrations(existing)
    else:
        engine = build_engine(config.get_main_option("sqlalchemy.url") or "")
        with engine.connect() as connection:
            run_migrations(connection)
        engine.dispose()
