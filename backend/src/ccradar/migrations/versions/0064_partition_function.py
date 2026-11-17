"""a narrow function so the worker can add future cost partitions (11.8)

Revision ID: 0064
Revises: 0063
Create Date: 2026-11-12

The runtime role cannot create tables (by design). Partitions used to be created only by
`ccradar migrate`, twelve months ahead, so an installation not upgraded for a year
put new rows in the DEFAULT partition. This SECURITY DEFINER function, owned by the
migration role, creates exactly one monthly partition of ``cost_line_items`` in a fixed
window (last month to 24 months ahead), with the same row-level security policy and no
direct access for the caller. The monthly ``costs.ensure_partitions`` job calls it.
"""

from collections.abc import Sequence

from alembic import op

from ccradar.db.rls import enable_tenant_rls_sql

revision: str = "0064"
down_revision: str | None = "0063"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PLACEHOLDER = "ccr_partition_placeholder"


def _policy_statements() -> str:
    """PL/pgSQL that applies the tenant policy to the new partition ``name``."""
    lines = []
    for stmt in enable_tenant_rls_sql(PLACEHOLDER):
        template = stmt.replace("'", "''")
        lines.append(f"  EXECUTE replace('{template}', '\"{PLACEHOLDER}\"', quote_ident(name));")
    return "\n".join(lines)


FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION ccradar_add_cost_partition(month date) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $fn$
DECLARE
  lower_bound date := date_trunc('month', month)::date;
  upper_bound date := (date_trunc('month', month) + interval '1 month')::date;
  name text := 'cost_line_items_y' || to_char(lower_bound, 'YYYY')
               || 'm' || to_char(lower_bound, 'MM');
BEGIN
  IF lower_bound < (date_trunc('month', now()) - interval '1 month')::date
     OR lower_bound > (date_trunc('month', now()) + interval '24 months')::date THEN
    RAISE EXCEPTION 'partition month % is outside the allowed window', lower_bound;
  END IF;
  IF to_regclass(quote_ident(name)) IS NOT NULL THEN
    RETURN false;
  END IF;
  EXECUTE format('CREATE TABLE %I PARTITION OF cost_line_items FOR VALUES FROM (%L) TO (%L)',
                 name, lower_bound, upper_bound);
@POLICY@
  EXECUTE format('REVOKE ALL ON %I FROM %I', name, session_user);
  RETURN true;
END
$fn$
"""


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(FUNCTION_SQL.replace("@POLICY@", _policy_statements()))
    # Only the function, not the table rights, is shared with the runtime role (migrate
    # grants EXECUTE on functions to it).
    op.execute("REVOKE ALL ON FUNCTION ccradar_add_cost_partition(date) FROM PUBLIC")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS ccradar_add_cost_partition(date)")
