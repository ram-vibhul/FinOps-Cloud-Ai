from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from ccradar.costs.ingest import CostDataError, CostLine, replace_billing_period
from ccradar.db import Database
from ccradar.db.base import utcnow
from ccradar.db.tenant import set_tenant
from ccradar.models import CostDaily, CostImportRun, CostLineItem
from ccradar.privacy import retention
from tests.conftest import POSTGRES_URL
from tests.helpers import make_org


def line(day: date, cost: str, service: str = "Amazon EC2", account: str = "1", **kw) -> CostLine:
    return CostLine(
        charge_date=day,
        provider="aws",
        service_name=service,
        sub_account_id=account,
        billed_cost=Decimal(cost),
        effective_cost=Decimal(cost),
        billing_currency="USD",
        region="ap-south-1",
        **kw,
    )


def daily_total(db: Database, org_id) -> Decimal:
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        return Decimal(str(s.scalar(select(func.sum(CostDaily.effective_cost))) or 0))


def line_count(db: Database, org_id) -> int:
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        return s.scalar(select(func.count()).select_from(CostLineItem)) or 0


def test_import_and_rollup(db: Database) -> None:
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    nov = date(2026, 11, 1)
    with db.session() as s:
        run = replace_billing_period(
            s,
            org_id=org_id,
            source_kind="csv",
            source_key="csv:1",
            billing_period=nov,
            lines=[
                line(nov, "10.5"),
                line(nov, "4.5"),
                line(nov, "3", service="Amazon S3"),
                line(nov + timedelta(days=1), "7", tags={"team": "data"}),
            ],
        )
        assert run.status == "succeeded" and run.rows == 4
        assert run.total_cost == Decimal("25") and run.currency == "USD"
    assert line_count(db, org_id) == 4
    assert daily_total(db, org_id) == Decimal("25")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        rows = s.execute(
            select(
                CostDaily.charge_date,
                CostDaily.service_name,
                CostDaily.effective_cost,
                CostDaily.line_count,
            ).order_by(CostDaily.charge_date, CostDaily.service_name)
        ).all()
    assert [(r[1], float(r[2]), r[3]) for r in rows] == [
        ("Amazon EC2", 15.0, 2),
        ("Amazon S3", 3.0, 1),
        ("Amazon EC2", 7.0, 1),
    ]


def test_reimport_replaces_not_duplicates(db: Database) -> None:
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    nov = date(2026, 11, 1)
    for cost in ("10", "12"):  # provider restated the bill
        with db.session() as s:
            replace_billing_period(
                s,
                org_id=org_id,
                source_kind="csv",
                source_key="csv:1",
                billing_period=nov,
                lines=[line(nov + timedelta(days=4), cost)],
            )
    assert line_count(db, org_id) == 1
    assert daily_total(db, org_id) == Decimal("12")
    # A restatement that drops a day also removes it from the rollup.
    with db.session() as s:
        replace_billing_period(
            s,
            org_id=org_id,
            source_kind="csv",
            source_key="csv:1",
            billing_period=nov,
            lines=[line(nov, "1")],
        )
    assert daily_total(db, org_id) == Decimal("1")


def test_sources_and_periods_are_independent(db: Database) -> None:
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    nov, dec = date(2026, 11, 1), date(2026, 12, 1)
    with db.session() as s:
        replace_billing_period(
            s,
            org_id=org_id,
            source_kind="csv",
            source_key="a",
            billing_period=nov,
            lines=[line(nov, "1")],
        )
        replace_billing_period(
            s,
            org_id=org_id,
            source_kind="csv",
            source_key="b",
            billing_period=nov,
            lines=[line(nov, "2")],
        )
        replace_billing_period(
            s,
            org_id=org_id,
            source_kind="csv",
            source_key="a",
            billing_period=dec,
            lines=[line(dec, "4")],
        )
        replace_billing_period(
            s,
            org_id=org_id,
            source_kind="csv",
            source_key="a",
            billing_period=nov,
            lines=[line(nov, "8")],
        )
    assert daily_total(db, org_id) == Decimal("14")


def test_other_org_untouched(db: Database) -> None:
    a, _ = make_org(db, "A", "a@a.com")
    b, _ = make_org(db, "B", "b@b.com")
    nov = date(2026, 11, 1)
    with db.session() as s:
        replace_billing_period(
            s,
            org_id=a,
            source_kind="csv",
            source_key="x",
            billing_period=nov,
            lines=[line(nov, "5")],
        )
        replace_billing_period(
            s,
            org_id=b,
            source_kind="csv",
            source_key="x",
            billing_period=nov,
            lines=[line(nov, "9")],
        )
    assert daily_total(db, a) == Decimal("5")
    assert daily_total(db, b) == Decimal("9")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"charge_category": "Refund"}, "charge category"),
        ({"pricing_category": "Cheap"}, "pricing category"),
        ({"billing_currency": "usd"}, "currency"),
        ({"service_name": ""}, "service name"),
    ],
)
def test_validation(db: Database, kwargs: dict, message: str) -> None:
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    bad = line(date(2026, 11, 1), "1")
    for k, v in kwargs.items():
        setattr(bad, k, v)
    with db.session() as s, pytest.raises(CostDataError, match=message):
        replace_billing_period(
            s,
            org_id=org_id,
            source_kind="csv",
            source_key="x",
            billing_period=date(2026, 11, 1),
            lines=[bad],
        )


def test_large_import_in_batches(db: Database) -> None:
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    nov = date(2026, 11, 1)
    lines = (line(nov + timedelta(days=i % 30), "0.01", resource_id=f"r{i}") for i in range(12_000))
    with db.session() as s:
        run = replace_billing_period(
            s, org_id=org_id, source_kind="csv", source_key="big", billing_period=nov, lines=lines
        )
    assert run.rows == 12_000
    assert daily_total(db, org_id) == Decimal("120.00")


def test_cost_retention_uses_dates(db: Database) -> None:
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    today = utcnow().date()
    old = today - timedelta(days=4000)
    with db.session() as s:
        replace_billing_period(
            s,
            org_id=org_id,
            source_kind="csv",
            source_key="x",
            billing_period=old,
            lines=[line(old, "1")],
        )
        replace_billing_period(
            s,
            org_id=org_id,
            source_kind="csv",
            source_key="x",
            billing_period=today,
            lines=[line(today, "2")],
        )
    with db.session() as s:
        totals = retention.apply_retention(s)
    assert totals["cost_line_items"] == 1 and totals["cost_daily"] == 1
    assert daily_total(db, org_id) == Decimal("2")
    kinds = {p.kind for p in retention.policies()}
    assert {"audit_log", "cost_line_items", "cost_daily"} <= kinds


def test_demo_costs(db: Database) -> None:
    from ccradar.demo.seed import ensure_demo, load_all_seeders, seeders

    load_all_seeders()
    if "costs.aws" not in seeders():  # another test reset the registry
        pytest.fail("demo cost seeder not registered")
    with db.session() as s:
        org = ensure_demo(s)
        org_id = org.id
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        days = s.scalar(select(func.count(func.distinct(CostDaily.charge_date))))
        accounts = s.scalar(select(func.count(func.distinct(CostDaily.sub_account_id))))
        runs = s.scalars(select(CostImportRun.status)).all()
        total = Decimal(str(s.scalar(select(func.sum(CostDaily.effective_cost)))))
    assert days == 90 and accounts == 5
    assert set(runs) == {"succeeded"}
    assert Decimal(300_000) < total < Decimal(800_000)


@pytest.mark.skipif(not POSTGRES_URL, reason="needs PostgreSQL")
def test_postgres_partitions(db: Database, admin_database_url: str) -> None:
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    today = utcnow().date()
    with db.session() as s:
        replace_billing_period(
            s,
            org_id=org_id,
            source_kind="csv",
            source_key="x",
            billing_period=today,
            lines=[line(today, "3")],
        )
    admin = Database(admin_database_url)
    with admin.engine.connect() as conn:
        partition = f"cost_line_items_y{today.year}m{today.month:02d}"
        rows = conn.execute(text(f"SELECT count(*) FROM {partition}")).scalar()  # noqa: S608
        assert rows == 1  # routed to its monthly partition
        rls = conn.execute(
            text("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = :n"),
            {"n": partition},
        ).one()
        assert tuple(rls) == (True, True)
    admin.dispose()
    denied = pytest.raises((ProgrammingError, DBAPIError), match="permission denied")
    with db.engine.connect() as conn, denied:  # app role: only through the parent
        conn.execute(text(f"SELECT * FROM {partition}"))  # noqa: S608
