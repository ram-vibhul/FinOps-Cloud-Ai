"""Tenant scoping primitives.

Every table holding customer data inherits :class:`TenantMixin`, which adds an
``org_id`` foreign key (cascade delete) and registers the table as tenant-scoped.
The registry drives row-level security (1.2), the application guard (1.3),
organisation export/deletion (1.14) and retention purges (1.15), so a new tenant
table is automatically covered by all of them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection, ForeignKey, Uuid, event, inspect, or_, text
from sqlalchemy.orm import (
    Mapped,
    ORMExecuteState,
    Session,
    SessionTransaction,
    declared_attr,
    mapped_column,
    with_loader_criteria,
)

from ccradar.db.base import Base

_TENANT_TABLES: set[str] = set()


class TenantIsolationError(RuntimeError):
    """A query or write would cross organisations. Always a programming error."""


class TenantMixin:
    __abstract__ = True
    # Name of a column holding a user id whose owner may *read* the row across
    # organisations (e.g. their own memberships). Mirrors the RLS policy.
    __tenant_user_column__: str | None = None

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        name = cls.__dict__.get("__tablename__")
        if isinstance(name, str):
            _TENANT_TABLES.add(name)

    @declared_attr
    def org_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(
            Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
        )


def tenant_tables() -> frozenset[str]:
    """Names of all tenant-scoped tables (models must be imported first)."""
    known = set(Base.metadata.tables)
    return frozenset(_TENANT_TABLES & known)


# --- per-transaction tenant context -------------------------------------------


@dataclass(frozen=True)
class TenantContext:
    org_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    bypass: bool = False  # cross-tenant system work only (purges, migrations, ops)


_INFO_KEY = "ccradar.tenant"
_EMPTY = TenantContext()


def current_tenant(session: Session) -> TenantContext:
    return session.info.get(_INFO_KEY, _EMPTY)


def set_tenant(
    session: Session,
    *,
    org_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    bypass: bool = False,
) -> TenantContext:
    """Scope this session to one organisation (and signed-in user).

    Stored on the session and applied as transaction-local PostgreSQL settings at
    the start of every transaction (and immediately if one is open), so it
    survives commits. On SQLite it only feeds the application guard.
    """
    ctx = TenantContext(org_id=org_id, user_id=user_id, bypass=bypass)
    session.info[_INFO_KEY] = ctx
    if session.in_transaction():
        _apply(session.connection(), ctx)
    return ctx


def set_user(session: Session, user_id: uuid.UUID) -> TenantContext:
    """Set the acting user while keeping the current organisation."""
    ctx = current_tenant(session)
    return set_tenant(session, org_id=ctx.org_id, user_id=user_id, bypass=ctx.bypass)


def _apply(connection: Connection, ctx: TenantContext) -> None:
    if connection.dialect.name != "postgresql":
        return
    connection.execute(
        text(
            "SELECT set_config('ccradar.org_id', :org, true),"
            " set_config('ccradar.user_id', :user, true),"
            " set_config('ccradar.bypass_rls', :bypass, true)"
        ),
        {
            "org": str(ctx.org_id) if ctx.org_id else "",
            "user": str(ctx.user_id) if ctx.user_id else "",
            "bypass": "on" if ctx.bypass else "off",
        },
    )


@event.listens_for(Session, "after_begin")
def _on_begin(session: Session, _tx: SessionTransaction, connection: Connection) -> None:
    ctx = session.info.get(_INFO_KEY)
    if ctx is not None:
        _apply(connection, ctx)


# --- application-level guard (1.3) --------------------------------------------
#
# Defence in depth next to PostgreSQL RLS, and the only isolation on SQLite:
#  * every ORM SELECT/UPDATE/DELETE touching a tenant model gets an automatic
#    ``org_id = <current org>`` criterion (plus "own rows" for user-column tables);
#  * querying a tenant model with no organisation (and no bypass) raises;
#  * flushing a tenant row for another organisation, or moving a row between
#    organisations, raises; a missing ``org_id`` is filled from the context.
# Raw ``text()`` SQL is not covered here; RLS covers it on PostgreSQL.

SKIP_GUARD_OPTION = "ccradar_skip_tenant_guard"


def _tenant_classes(session: Session) -> list[type[Any]]:
    return [
        m.class_
        for m in Base.registry.mappers
        if issubclass(m.class_, TenantMixin) and m.class_.__dict__.get("__tablename__")
    ]


def _criterion(cls: type[Any], ctx: TenantContext) -> Any:
    user_col = getattr(cls, "__tenant_user_column__", None)
    parts = []
    if ctx.org_id is not None:
        parts.append(cls.org_id == ctx.org_id)
    if user_col and ctx.user_id is not None:
        parts.append(getattr(cls, user_col) == ctx.user_id)
    return or_(*parts) if len(parts) > 1 else parts[0]


@event.listens_for(Session, "do_orm_execute")
def _scope_queries(state: ORMExecuteState) -> None:
    if not (state.is_select or state.is_update or state.is_delete):
        return
    if state.execution_options.get(SKIP_GUARD_OPTION):
        return
    ctx = current_tenant(state.session)
    if ctx.bypass:
        return
    involved = [m.class_ for m in state.all_mappers if issubclass(m.class_, TenantMixin)]
    if not involved and not state.is_select:
        return
    if ctx.org_id is None:
        no_user_path = [
            c.__name__
            for c in involved
            if not (getattr(c, "__tenant_user_column__", None) and ctx.user_id is not None)
        ]
        if no_user_path:
            raise TenantIsolationError(
                f"query on tenant model(s) {', '.join(no_user_path)} without an organisation "
                "context; call set_tenant() first"
            )
    options = []
    for cls in _tenant_classes(state.session):
        if ctx.org_id is None and not (
            getattr(cls, "__tenant_user_column__", None) and ctx.user_id is not None
        ):
            continue
        options.append(with_loader_criteria(cls, _criterion(cls, ctx), include_aliases=True))
    if options:
        state.statement = state.statement.options(*options)


@event.listens_for(Session, "before_flush")
def _check_writes(session: Session, _flush_context: Any, _instances: Any) -> None:
    ctx = current_tenant(session)
    for obj in session.new:
        if not isinstance(obj, TenantMixin):
            continue
        org_id = obj.__dict__.get("org_id")
        if ctx.bypass:
            continue
        if ctx.org_id is None:
            raise TenantIsolationError(
                f"cannot create {type(obj).__name__} without an organisation context"
            )
        if org_id is None:
            obj.org_id = ctx.org_id
        elif org_id != ctx.org_id:
            raise TenantIsolationError(
                f"cannot create {type(obj).__name__} in another organisation"
            )
    for obj in list(session.dirty) + list(session.deleted):
        if not isinstance(obj, TenantMixin) or ctx.bypass:
            continue
        history = inspect(obj).attrs.org_id.history
        if history.has_changes():
            raise TenantIsolationError(f"cannot move {type(obj).__name__} between organisations")
        if obj.org_id != ctx.org_id:
            raise TenantIsolationError(
                f"cannot change or delete {type(obj).__name__} of another organisation"
            )
