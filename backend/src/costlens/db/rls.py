"""PostgreSQL row-level security (RLS) for tenant tables.

Every tenant table gets ``ENABLE`` + ``FORCE ROW LEVEL SECURITY`` and one policy,
``tenant_isolation``: a row is visible/writable only when its ``org_id`` equals the
transaction's ``costlens.org_id`` setting, or when ``costlens.bypass_rls`` is on
(cross-tenant system jobs). Tables with a user column (memberships) additionally
let a user *read* their own rows across organisations (organisation switcher).
With no setting, nothing is visible: the default is deny.

The application connects as a non-owner role without ``BYPASSRLS``; migrations
run as the owner. Settings are applied per transaction by :mod:`costlens.db.tenant`.
"""

from __future__ import annotations

import re

from sqlalchemy import Connection, text

POLICY_NAME = "tenant_isolation"
_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")

HELPER_FUNCTIONS_SQL = [
    """
    CREATE OR REPLACE FUNCTION costlens_tenant_ok(row_org uuid) RETURNS boolean
    LANGUAGE sql STABLE AS $$
        SELECT coalesce(current_setting('costlens.bypass_rls', true), '') = 'on'
            OR row_org = nullif(current_setting('costlens.org_id', true), '')::uuid
    $$
    """,
    """
    CREATE OR REPLACE FUNCTION costlens_current_user_id() RETURNS uuid
    LANGUAGE sql STABLE AS $$
        SELECT nullif(current_setting('costlens.user_id', true), '')::uuid
    $$
    """,
]

DROP_HELPER_FUNCTIONS_SQL = [
    "DROP FUNCTION IF EXISTS costlens_tenant_ok(uuid)",
    "DROP FUNCTION IF EXISTS costlens_current_user_id()",
]


def ident(name: str) -> str:
    if not _IDENT.match(name):
        raise ValueError(f"unsafe SQL identifier: {name!r}")
    return f'"{name}"'


def enable_tenant_rls_sql(table: str, *, user_column: str | None = None) -> list[str]:
    t = ident(table)
    using = "costlens_tenant_ok(org_id)"
    if user_column:
        using += f" OR {ident(user_column)} = costlens_current_user_id()"
    return [
        f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY",
        f"DROP POLICY IF EXISTS {POLICY_NAME} ON {t}",
        f"CREATE POLICY {POLICY_NAME} ON {t} USING ({using}) "
        "WITH CHECK (costlens_tenant_ok(org_id))",
    ]


def disable_tenant_rls_sql(table: str) -> list[str]:
    t = ident(table)
    return [
        f"DROP POLICY IF EXISTS {POLICY_NAME} ON {t}",
        f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY",
        f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY",
    ]


def grant_app_role(conn: Connection, role: str) -> None:
    """Give the runtime role data access only (no DDL, no ownership)."""
    r = ident(role)
    for stmt in (
        f"GRANT USAGE ON SCHEMA public TO {r}",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {r}",
        f"GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO {r}",
        f"GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO {r}",
        f"REVOKE INSERT, UPDATE, DELETE ON alembic_version FROM {r}",
    ):
        conn.execute(text(stmt))


def is_privileged_role(conn: Connection) -> bool:
    """True when the connected role ignores RLS (superuser or BYPASSRLS)."""
    row = conn.execute(
        text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user")
    ).scalar()
    return bool(row)


def rls_status(conn: Connection) -> dict[str, tuple[bool, bool, bool]]:
    """table -> (rls enabled, rls forced, tenant policy present)."""
    rows = conn.execute(
        text(
            """
            SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
                   EXISTS (SELECT 1 FROM pg_policies p
                           WHERE p.tablename = c.relname AND p.policyname = :policy)
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = current_schema() AND c.relkind = 'r'
            """
        ),
        {"policy": POLICY_NAME},
    ).all()
    return {name: (bool(e), bool(f), bool(p)) for name, e, f, p in rows}
