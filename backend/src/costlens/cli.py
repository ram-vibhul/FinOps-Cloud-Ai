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


def migrate(database_url: str | None = None, revision: str = "head") -> None:
    """Apply Alembic migrations, plus the Procrastinate queue schema on PostgreSQL."""
    url = database_url or get_settings().database_url
    command.upgrade(alembic_config(url), revision)
    if url.startswith(("postgresql", "postgres")):
        apply_queue_schema(url)


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
    from costlens.jobs.runner import ProcrastinateRunner, build_procrastinate_app

    settings = get_settings()
    if settings.run_jobs_in_process:
        print("jobs run in-process with this configuration; no worker needed", file=sys.stderr)
        return 1
    app = build_procrastinate_app(settings.database_url)
    ProcrastinateRunner(app)
    with app.open():
        app.run_worker()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
