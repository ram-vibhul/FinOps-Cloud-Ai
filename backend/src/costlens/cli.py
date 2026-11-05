"""Command-line entry point: ``costlens <command>``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config

from costlens.config import get_settings


def alembic_config(database_url: str | None = None) -> Config:
    here = Path(__file__).resolve().parent
    cfg = Config()
    cfg.set_main_option("script_location", str(here / "migrations"))
    cfg.set_main_option("sqlalchemy.url", database_url or get_settings().database_url)
    return cfg


def _url_user(url: str) -> str | None:
    from sqlalchemy.engine import make_url

    return make_url(url).username


def migrate(
    database_url: str | None = None, revision: str = "head", *, app_role: str | None = None
) -> None:
    """Apply Alembic migrations; on PostgreSQL also the queue schema and app-role grants.

    Without arguments, uses ``COSTLENS_MIGRATION_DATABASE_URL`` (falling back to
    ``COSTLENS_DATABASE_URL``) and grants data access to the runtime role when the
    two URLs use different users.
    """
    if database_url is None:
        settings = get_settings()
        database_url = settings.migration_database_url or settings.database_url
        runtime_user = _url_user(settings.database_url)
        if app_role is None and runtime_user and runtime_user != _url_user(database_url):
            app_role = runtime_user
    command.upgrade(alembic_config(database_url), revision)
    if database_url.startswith(("postgresql", "postgres")):
        apply_queue_schema(database_url)
        if app_role:
            from costlens.db.rls import grant_app_role
            from costlens.db.session import build_engine

            engine = build_engine(database_url)
            with engine.begin() as conn:
                grant_app_role(conn, app_role)
            engine.dispose()


def apply_queue_schema(url: str) -> None:
    from sqlalchemy import inspect

    from costlens.db.session import build_engine
    from costlens.jobs.runner import build_procrastinate_app

    engine = build_engine(url)
    try:
        if inspect(engine).has_table("procrastinate_jobs"):
            return
    finally:
        engine.dispose()
    app = build_procrastinate_app(url)
    with app.open():
        app.schema_manager.apply_schema()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="costlens")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="apply database migrations")
    serve = sub.add_parser("serve", help="run the web server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    sub.add_parser("worker", help="run the background job worker (PostgreSQL only)")
    args = parser.parse_args(argv)

    if args.command == "migrate":
        migrate()
        return 0
    if args.command == "serve":
        import uvicorn

        uvicorn.run(
            "costlens.asgi:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
            proxy_headers=True,
        )
        return 0
    if args.command == "worker":
        return run_worker()
    return 2


def run_worker() -> int:
    import asyncio

    from costlens.core.logging import configure_logging
    from costlens.jobs.runner import ProcrastinateRunner, build_procrastinate_app, run_worker_async

    settings = get_settings()
    configure_logging(settings.log_level, settings.effective_log_format)
    if settings.run_jobs_in_process:
        print("jobs run in-process with this configuration; no worker needed", file=sys.stderr)
        return 1
    app = build_procrastinate_app(settings.database_url)
    ProcrastinateRunner(app)
    asyncio.run(run_worker_async(app))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
