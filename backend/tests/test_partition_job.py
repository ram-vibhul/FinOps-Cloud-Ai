"""Future cost partitions without an upgrade (11.8), on PostgreSQL."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import text

from ccradar.costs.partitions import _add_months, add_future_partitions, partition_name
from ccradar.db import Database
from tests.conftest import APP_ROLE, POSTGRES_URL

pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="needs PostgreSQL")


def test_the_worker_role_adds_next_years_partitions(db: Database) -> None:
    # `db` runs as the restricted runtime role, exactly like the worker.
    today = date.today().replace(day=1)
    far = _add_months(today, 14)  # migrate only creates 12 months ahead
    with db.engine.begin() as conn:
        add_future_partitions(conn, today=today, months_ahead=14)
        name = partition_name(far)
        assert conn.execute(text("SELECT to_regclass(:n)"), {"n": name}).scalar() is not None
        forced = conn.execute(
            text("SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE relname = :n"),
            {"n": name},
        ).scalar()
        assert forced is True  # same tenant policy as every other partition
        direct = conn.execute(
            text("SELECT has_table_privilege(:r, :n, 'SELECT')"), {"r": APP_ROLE, "n": name}
        ).scalar()
        assert direct is False  # rows are reached through the parent table only
        assert add_future_partitions(conn, today=today, months_ahead=14) == []  # idempotent


def test_the_function_only_does_its_one_job(db: Database) -> None:
    with db.engine.connect() as conn:
        with pytest.raises(Exception, match="outside the allowed window"):
            conn.execute(text("SELECT ccradar_add_cost_partition(:m)"), {"m": date(2099, 1, 1)})
        conn.rollback()
        with pytest.raises(Exception, match="permission denied"):
            conn.execute(text("CREATE TABLE sneaky (id int)"))  # still no general rights
