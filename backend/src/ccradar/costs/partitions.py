"""Monthly partitions for ``cost_line_items`` on PostgreSQL.

The table is created partitioned by ``charge_date`` with a DEFAULT partition, so
inserts always work; monthly partitions keep large customers fast (pruning,
cheap retention). ``ccradar migrate`` creates partitions from three years back to
one year ahead, as the owner role, and gives each partition the same RLS policy
and no direct access for the application role (it must go through the parent).
"""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import Connection, text

from ccradar.db.rls import enable_tenant_rls_sql, ident

log = logging.getLogger(__name__)
PARENT = "cost_line_items"


def _add_months(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def partition_name(month: date) -> str:
    return f"{PARENT}_y{month.year}m{month.month:02d}"


def ensure_partitions(
    conn: Connection,
    *,
    today: date,
    months_back: int = 36,
    months_ahead: int = 12,
    app_role: str | None = None,
) -> list[str]:
    existing = set(
        conn.execute(
            text(
                "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
                "JOIN pg_class p ON p.oid = i.inhparent WHERE p.relname = :parent"
            ),
            {"parent": PARENT},
        ).scalars()
    )
    created = []
    start = today.replace(day=1)
    for offset in range(-months_back, months_ahead + 1):
        month = _add_months(start, offset)
        name = partition_name(month)
        if name in existing:
            continue
        savepoint = conn.begin_nested()
        try:
            lower, upper = month.isoformat(), _add_months(month, 1).isoformat()
            conn.execute(
                text(
                    f"CREATE TABLE {ident(name)} PARTITION OF {ident(PARENT)} "
                    f"FOR VALUES FROM ('{lower}') TO ('{upper}')"
                )
            )
            for stmt in enable_tenant_rls_sql(name):
                conn.execute(text(stmt))
            if app_role:
                conn.execute(text(f"REVOKE ALL ON {ident(name)} FROM {ident(app_role)}"))
            savepoint.commit()
            created.append(name)
        except Exception as exc:  # e.g. rows for that month already sit in DEFAULT
            savepoint.rollback()
            log.warning("could not create partition %s: %s", name, exc)
    return created
