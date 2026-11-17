"""Load test for large bills (11.18).

Fills a scratch PostgreSQL database with a synthetic bill of ``--rows`` cost lines for
one organisation (a year and a month of daily lines: 50 accounts, 30 services, 12
regions, ``team``/``env`` tags), then runs the application's own cost queries as the
runtime role, with row-level security on, and reports how long each took. The slowest
SQL statements are shown with their PostgreSQL plans, to see where an index helps.

Uses the test container from ``make test-pg`` and its own database (``ccradar_load``);
it never touches the test database or anything else. Run from the repository root:

    backend/.venv/bin/python tools/loadtest.py --rows 5000000

Writing 50 million rows takes about 20 GB of disk and a long time; start smaller.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date, timedelta
from typing import Any

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url

DEFAULT_URL = "postgresql+psycopg://ccradar:ccradar@localhost:55432/ccradar_load"
APP_ROLE, APP_PASSWORD = "ccradar_app_load", "app-load-password"  # noqa: S105 - scratch DB
REGIONS = ["us-east-1", "us-east-2", "us-west-2", "eu-west-1", "eu-central-1", "ap-south-1",
           "ap-southeast-1", "ap-southeast-2", "ap-northeast-1", "sa-east-1", "ca-central-1",
           "eu-north-1"]  # fmt: skip


def say(*parts: Any) -> None:
    print(*parts, flush=True)


def create_database(url: str) -> None:
    target = make_url(url)
    admin = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        if not conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :d"),
                            {"d": target.database}).scalar():  # fmt: skip
            conn.execute(text(f'CREATE DATABASE "{target.database}"'))
        if not conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"),
                            {"r": APP_ROLE}).scalar():  # fmt: skip
            conn.execute(text(f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PASSWORD}'"))
    admin.dispose()


def fill(url: str, rows: int, days: int, end: date) -> uuid.UUID:
    from ccradar.cli import migrate

    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    migrate(url, app_role=APP_ROLE, partition_months=(days // 28 + 2, 1))
    org_id = uuid.uuid4()
    resources = max(rows // days, 1)
    start = end - timedelta(days=days - 1)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO organizations (id, name, slug, settings, is_demo, default_currency,"
                " created_at, updated_at) VALUES (:id, 'Load test', 'load-test', '{}', false,"
                " 'USD', now(), now())"
            ),
            {"id": org_id},
        )
    say(f"Writing {resources * days:,} lines ({resources:,} resources x {days} days)…")
    began = time.monotonic()
    day = start
    while day <= end:
        chunk_end = min(day + timedelta(days=30), end)
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
INSERT INTO cost_line_items (id, org_id, charge_date, billing_period, source_key, provider,
  sub_account_id, service_name, region, resource_id, usage_type, charge_category,
  pricing_category, billed_cost, effective_cost, list_cost, billing_currency,
  tags, vtags, extras)
SELECT gen_random_uuid(), :org, d::date, date_trunc('month', d)::date, 'load', 'aws',
  lpad((r % 50)::text, 12, '0'), 'Service' || (r % 30), (CAST(:regions AS text[]))[1 + r % 12],
  'arn:aws:ec2:' || r, 'Usage' || (r % 200), 'Usage', 'Standard',
  c, c * 0.9, c * 1.1, 'USD',
  json_build_object('team', 'team' || (r % 40), 'env', CASE WHEN r % 3 = 0 THEN 'prod'
    ELSE 'dev' END), '{}'::json, '{}'::json
FROM generate_series(CAST(:start AS date), CAST(:end AS date), interval '1 day') d,
  generate_series(0, :n - 1) r,
  LATERAL (SELECT round((random() * 10 + (r % 7))::numeric, 6) AS c) x
"""
                ),
                {"org": org_id, "start": day, "end": chunk_end, "n": resources,
                 "regions": REGIONS},  # fmt: skip
            )
        say(f"  through {chunk_end} ({time.monotonic() - began:.0f} s)")
        day = chunk_end + timedelta(days=1)
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text("VACUUM ANALYZE cost_line_items"))
        size = conn.execute(text("SELECT pg_size_pretty(pg_database_size(current_database()))"))
        say(f"Database size {size.scalar()}")
    engine.dispose()
    return org_id


class Timer:
    def __init__(self) -> None:
        self.statements: list[tuple[float, str, Any]] = []
        self.results: list[tuple[str, float]] = []

    def listen(self, engine: Any) -> None:
        @event.listens_for(engine, "before_cursor_execute")
        def before(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
            conn.info["ccradar.started"] = time.monotonic()

        @event.listens_for(engine, "after_cursor_execute")
        def after(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
            took = time.monotonic() - conn.info.pop("ccradar.started", time.monotonic())
            if statement.lstrip().upper().startswith("SELECT"):
                self.statements.append((took, statement, parameters))

    @contextmanager
    def step(self, name: str) -> Iterator[None]:
        began, first = time.monotonic(), len(self.statements)
        yield
        took = time.monotonic() - began
        self.results.append((name, took))
        mine = [t for t, _, _ in self.statements[first:]]
        detail = f"{len(mine)} queries, slowest {max(mine) * 1000:.0f} ms" if mine else ""
        say(f"  {took * 1000:>9.0f} ms  {name}  ({detail})")


def run_queries(url: str, org_id: uuid.UUID, end: date, explain: int) -> None:
    from ccradar.costs import anomalies, explorer
    from ccradar.costs.ingest import refresh_daily
    from ccradar.db import Database
    from ccradar.db.tenant import set_tenant
    from ccradar.inventory.service import resource_costs

    app_url = make_url(url).set(username=APP_ROLE, password=APP_PASSWORD)
    db = Database(app_url.render_as_string(hide_password=False))
    timer = Timer()
    timer.listen(db.engine)
    month_ago = end - timedelta(days=29)

    def explore(**kw: Any) -> Callable[[Any], Any]:
        return lambda s: explorer.explore(s, org_id, explorer.ExploreQuery(**kw))

    steps: list[tuple[str, Callable[[Any], Any]]] = [
        ("daily rollup, whole bill (after an import)",
         lambda s: refresh_daily(s, org_id=org_id, start=end - timedelta(days=400), end=end)),
        ("explorer: by service, 30 days, daily", explore(start=month_ago, end=end)),
        ("explorer: by account, 12 months, monthly",
         explore(start=end - timedelta(days=364), end=end, group_by=["account"],
                 granularity="month")),
        ("explorer: by tag team, 30 days", explore(start=month_ago, end=end,
                                                   group_by=["tag:team"])),
        ("explorer: by resource, 30 days, top 100", explore(start=month_ago, end=end,
                                                             group_by=["resource"],
                                                             granularity="total")),
        ("explorer: tag env=prod by service, 90 days",
         explore(start=end - timedelta(days=89), end=end, filters={"tag:env": ["prod"]})),
        ("inventory: 30-day cost of 500 resources",
         lambda s: resource_costs(s, org_id, [f"arn:aws:ec2:{i}" for i in range(500)])),
        ("anomaly detection", lambda s: anomalies.detect(s, org_id, through=end)),
    ]  # fmt: skip
    say("\nTimings (runtime role, row-level security on):")
    for name, func in steps:
        with db.session() as s:
            set_tenant(s, org_id=org_id)
            with timer.step(name):
                func(s)
    slowest = sorted(timer.statements, key=lambda x: -x[0])[:explain]
    for took, statement, params in slowest:
        say(f"\n--- {took * 1000:.0f} ms\n{statement.strip()[:600]}")
        with db.session() as s:
            set_tenant(s, org_id=org_id)
            plan = s.connection().exec_driver_sql(
                "EXPLAIN (ANALYZE, BUFFERS) " + statement, params
            )
            for (line,) in list(plan)[:25]:
                say("   ", line)
    db.engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--url", default=os.environ.get("CCRADAR_LOAD_DATABASE_URL", DEFAULT_URL))
    parser.add_argument("--rows", type=int, default=1_000_000)
    parser.add_argument("--days", type=int, default=395)
    parser.add_argument("--explain", type=int, default=3, help="plans for the N slowest")
    parser.add_argument("--reuse", action="store_true", help="skip writing; query what is there")
    args = parser.parse_args(argv)
    if "ccradar_load" not in args.url:
        parser.error("refusing: the database name must contain ccradar_load")
    end = date.today() - timedelta(days=1)
    create_database(args.url)
    if args.reuse:
        engine = create_engine(args.url)
        with engine.connect() as conn:
            org_id = conn.execute(text("SELECT id FROM organizations WHERE slug='load-test'"))
            org_id = org_id.scalar_one()
            end = conn.execute(text("SELECT max(charge_date) FROM cost_line_items")).scalar_one()
        engine.dispose()
    else:
        org_id = fill(args.url, args.rows, args.days, end)
    run_queries(args.url, org_id, end, args.explain)
    return 0


if __name__ == "__main__":
    sys.exit(main())
