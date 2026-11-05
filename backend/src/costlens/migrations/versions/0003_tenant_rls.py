"""tenant row-level security (PostgreSQL only)

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-05

New tenant tables must call ``enable_tenant_rls_sql`` in their own migration;
``tests/test_rls.py`` fails if any tenant table is left unprotected.
"""

from collections.abc import Sequence

from alembic import op

from costlens.db.rls import (
    DROP_HELPER_FUNCTIONS_SQL,
    HELPER_FUNCTIONS_SQL,
    disable_tenant_rls_sql,
    enable_tenant_rls_sql,
)

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _postgres():
        return
    for stmt in HELPER_FUNCTIONS_SQL:
        op.execute(stmt)
    for stmt in enable_tenant_rls_sql("memberships", user_column="user_id"):
        op.execute(stmt)


def downgrade() -> None:
    if not _postgres():
        return
    for stmt in disable_tenant_rls_sql("memberships"):
        op.execute(stmt)
    for stmt in DROP_HELPER_FUNCTIONS_SQL:
        op.execute(stmt)
