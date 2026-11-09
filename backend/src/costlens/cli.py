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
    database_url: str | None = None,
    revision: str = "head",
    *,
    app_role: str | None = None,
    partition_months: tuple[int, int] = (36, 12),
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
        from datetime import date

        from costlens.costs.partitions import ensure_partitions
        from costlens.db.rls import grant_app_role
        from costlens.db.session import build_engine

        engine = build_engine(database_url)
        with engine.begin() as conn:
            if app_role:
                grant_app_role(conn, app_role)
            ensure_partitions(conn, today=date.today(), app_role=app_role)
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="costlens")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="apply database migrations")
    serve = sub.add_parser("serve", help="run the web server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    sub.add_parser("worker", help="run the background job worker (PostgreSQL only)")
    demo = sub.add_parser("demo", help="create or reset the demo organisation")
    demo.add_argument("action", choices=["seed", "reset"])
    sub.add_parser(
        "rotate-keys", help="re-encrypt stored secrets with the first COSTLENS_ENCRYPTION_KEYS key"
    )
    lab = sub.add_parser("lab", help="score the Kubernetes diagnosis engine on recorded failures")
    lab.add_argument("action", choices=["run"])
    lab.add_argument("--record", metavar="FILE", help="append the score to this JSON-lines file")
    lab.add_argument(
        "--min", type=float, default=0.0, help="exit with an error below this accuracy"
    )
    est = sub.add_parser(
        "estimate", help="estimate the monthly cost change of a Terraform plan or K8s manifests"
    )
    est.add_argument("kind", choices=["terraform", "kubernetes"])
    est.add_argument("file", help="terraform show -json output, or manifests (YAML/JSON)")
    est.add_argument("--before", metavar="FILE", help="kubernetes: the current manifests")
    est.add_argument(
        "--prices", metavar="FILE", help="prices from GET /api/v1/estimates/prices (your bill)"
    )
    est.add_argument("--format", choices=["markdown", "json"], default="markdown")
    est.add_argument(
        "--fail-above",
        type=float,
        metavar="AMOUNT",
        help="exit with code 2 when the monthly increase is above this amount",
    )
    wh = sub.add_parser("worker-health", help="exit 0 if this worker ran its heartbeat lately")
    wh.add_argument("--max-age", type=int, default=900, help="seconds (default 900)")
    bak = sub.add_parser("backup", help="back up the database and files, or verify a backup")
    bak.add_argument("action", choices=["create", "verify"])
    bak.add_argument("file", nargs="?", help="create: where to write it; verify: the backup")
    res = sub.add_parser("restore", help="replace the database and files with a backup")
    res.add_argument("file")
    res.add_argument("--yes", action="store_true", help="confirm: current data is replaced")
    lic = sub.add_parser("licence", help="show, install or remove the self-hosted licence")
    lic.add_argument("action", choices=["status", "install", "remove"])
    lic.add_argument("file", nargs="?", help="install: a file holding the licence key")
    adm = sub.add_parser(
        "platform-admin", help="let someone manage this installation (licence, partners)"
    )
    adm.add_argument("action", choices=["add", "remove", "list"])
    adm.add_argument("email", nargs="?")
    return parser


def _worker_health(args: argparse.Namespace) -> int:
    from costlens.jobs.system import worker_healthy

    ok = worker_healthy(args.max_age)
    print("worker healthy" if ok else "no recent heartbeat", file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


def _backup(args: argparse.Namespace) -> int:
    from datetime import UTC, datetime
    from pathlib import Path

    from costlens import backup

    settings = get_settings()
    try:
        if args.action == "verify":
            if not args.file:
                print("give the backup file to verify", file=sys.stderr)
                return 2
            manifest = backup.verify(Path(args.file))
            print(
                f"backup is readable: {manifest['database']}, "
                f"CostLens {manifest['costlens_version']}, made {manifest['created_at']}, "
                f"{manifest['files']} files"
            )
            return 0
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        out = Path(args.file or f"costlens-backup-{stamp}.tar.gz")
        manifest = backup.create(settings, out)
    except (backup.BackupError, OSError) as exc:
        print(f"backup failed: {exc}", file=sys.stderr)
        return 1
    print(f"backup written to {out} ({manifest['database']}, {manifest['files']} files)")
    print(backup.KEY_REMINDER, file=sys.stderr)
    return 0


def _restore(args: argparse.Namespace) -> int:
    from pathlib import Path

    from costlens import backup

    if not args.yes:
        print(
            "restore replaces this installation's database and files with the backup's. "
            "Stop the web and worker processes, then run again with --yes.",
            file=sys.stderr,
        )
        return 2
    try:
        manifest = backup.restore(get_settings(), Path(args.file))
    except (backup.BackupError, OSError) as exc:
        print(f"restore failed: {exc}", file=sys.stderr)
        return 1
    print(f"restored the backup made {manifest['created_at']}; start CostLens again")
    return 0


def _licence(args: argparse.Namespace) -> int:
    import json
    from pathlib import Path

    from costlens.billing import licence
    from costlens.db import Database

    settings = get_settings()
    db = Database(settings.migration_database_url or settings.database_url)
    try:
        with db.session() as session:
            if args.action == "install":
                if not args.file:
                    print("give the file that holds the licence key", file=sys.stderr)
                    return 2
                try:
                    licence.install(session, Path(args.file).read_text(encoding="utf-8"))
                except licence.LicenceError as exc:
                    print(f"not installed: {exc}", file=sys.stderr)
                    return 1
            elif args.action == "remove":
                licence.remove(session)
            print(json.dumps(licence.status(session, settings), indent=2))
    finally:
        db.dispose()
    return 0


def _platform_admin(args: argparse.Namespace) -> int:
    from sqlalchemy import func, select

    from costlens.db import Database
    from costlens.db.tenant import set_tenant
    from costlens.models import User

    settings = get_settings()
    db = Database(settings.migration_database_url or settings.database_url)
    try:
        with db.session() as session:
            set_tenant(session, bypass=True)
            if args.action == "list":
                for email in session.scalars(
                    select(User.email).where(User.is_platform_admin.is_(True))
                ):
                    print(email)
                return 0
            if not args.email:
                print("give the person's email address", file=sys.stderr)
                return 2
            user = session.scalar(
                select(User).where(func.lower(User.email) == args.email.strip().lower())
            )
            if user is None:
                print("no such user: they must sign in once first", file=sys.stderr)
                return 1
            user.is_platform_admin = args.action == "add"
            print(f"{user.email}: platform admin = {user.is_platform_admin}")
    finally:
        db.dispose()
    return 0


def _estimate(args: argparse.Namespace) -> int:
    import json
    from pathlib import Path

    from costlens import estimate

    text = Path(args.file).read_text(encoding="utf-8")
    prices: dict = {"currency": "USD", "prices": {}}
    if args.prices:
        prices = json.loads(Path(args.prices).read_text(encoding="utf-8"))
    if args.kind == "terraform":
        result = estimate.terraform(json.loads(text), prices.get("prices"), prices["currency"])
    else:
        before = Path(args.before).read_text(encoding="utf-8") if args.before else ""
        result = estimate.kubernetes(text, before)
    out = (
        json.dumps(result.to_dict(), indent=2)
        if args.format == "json"
        else estimate.markdown(result)
    )
    print(out)
    if args.fail_above is not None and result.change > args.fail_above:
        return 2
    return 0


def _migrate(args: argparse.Namespace) -> int:
    migrate()
    return 0


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run(
        "costlens.asgi:app", host=args.host, port=args.port, reload=args.reload, proxy_headers=True
    )
    return 0


def _demo(args: argparse.Namespace) -> int:
    from costlens.core.crypto import configure_encryption
    from costlens.db import Database
    from costlens.demo.seed import ensure_demo, reset_demo
    from costlens.main import build_secret_box

    settings = get_settings()
    configure_encryption(build_secret_box(settings))
    db = Database(settings.migration_database_url or settings.database_url)
    with db.session() as session:
        org = ensure_demo(session) if args.action == "seed" else reset_demo(session)
        print(f"demo organisation ready: {org.id}")
    db.dispose()
    return 0


def _rotate_keys(args: argparse.Namespace) -> int:
    from costlens.db import Database
    from costlens.db.keys import rotate_encryption_keys
    from costlens.main import build_secret_box

    settings = get_settings()
    box = build_secret_box(settings)
    db = Database(settings.migration_database_url or settings.database_url)
    counts = rotate_encryption_keys(db, box)
    db.dispose()
    for name, n in counts.items():
        print(f"{name}: {n} re-encrypted")
    return 0


def _lab(args: argparse.Namespace) -> int:
    from pathlib import Path

    from costlens import __version__
    from costlens.k8s import lab as k8s_lab

    report = k8s_lab.run()
    k8s_lab.table(report)
    if args.record:
        k8s_lab.record(report, Path(args.record), __version__)
    return 0 if (report["accuracy"] or 0) >= args.min else 1


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    handlers = {
        "migrate": _migrate,
        "serve": _serve,
        "worker": lambda _: run_worker(),
        "demo": _demo,
        "rotate-keys": _rotate_keys,
        "lab": _lab,
        "estimate": _estimate,
        "licence": _licence,
        "backup": _backup,
        "worker-health": _worker_health,
        "restore": _restore,
        "platform-admin": _platform_admin,
    }
    return handlers[args.command](args)


def run_worker() -> int:
    import asyncio

    from costlens.core.logging import configure_logging
    from costlens.jobs.runner import ProcrastinateRunner, build_procrastinate_app, run_worker_async

    settings = get_settings()
    configure_logging(settings.log_level, settings.effective_log_format)
    if settings.run_jobs_in_process:
        print("jobs run in-process with this configuration; no worker needed", file=sys.stderr)
        return 1
    from costlens.core.crypto import configure_encryption
    from costlens.core.email import build_sender, configure_email
    from costlens.core.storage import build_storage
    from costlens.db import Database
    from costlens.jobs import load_all_jobs
    from costlens.jobs.context import JobContext, configure_jobs
    from costlens.main import build_secret_box

    configure_encryption(build_secret_box(settings))
    configure_email(build_sender(settings))
    from costlens.core.error_tracking import configure as configure_error_tracking

    configure_error_tracking(settings)
    configure_jobs(
        JobContext(
            settings=settings,
            db=Database(settings.database_url),
            storage=build_storage(settings.data_dir),
        )
    )
    load_all_jobs()
    app = build_procrastinate_app(settings.database_url)
    ProcrastinateRunner(app)
    asyncio.run(run_worker_async(app))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
