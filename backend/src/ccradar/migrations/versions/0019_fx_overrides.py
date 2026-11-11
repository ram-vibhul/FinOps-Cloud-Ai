"""exchange-rate overrides per organisation

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-05 15:10:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from ccradar.db.rls import enable_tenant_rls_sql

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "fx_overrides",
        sa.Column("from_currency", sa.String(length=3), nullable=False),
        sa.Column("to_currency", sa.String(length=3), nullable=False),
        sa.Column("start_day", sa.Date(), nullable=False),
        sa.Column("end_day", sa.Date(), nullable=False),
        sa.Column("rate", sa.Numeric(precision=20, scale=10), nullable=False),
        sa.Column("note", sa.String(length=300), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_fx_overrides_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fx_overrides")),
        sa.UniqueConstraint(
            "org_id", "from_currency", "to_currency", "start_day", name="uq_fx_overrides_start"
        ),
    )
    with op.batch_alter_table("fx_overrides", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_fx_overrides_org_id"), ["org_id"], unique=False)
    if op.get_bind().dialect.name == "postgresql":
        for stmt in enable_tenant_rls_sql("fx_overrides"):
            op.execute(stmt)


def downgrade() -> None:
    with op.batch_alter_table("fx_overrides", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_fx_overrides_org_id"))
    op.drop_table("fx_overrides")
