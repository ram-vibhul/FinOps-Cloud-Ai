"""Automatic audit capture for every change to audited models.

Models opt in with ``__audited__ = True``; ``__audit_redact__`` names columns
whose values must never be stored (only "changed" is recorded). A
``before_flush`` hook turns inserts, updates and deletes into ``AuditEvent``
rows in the same transaction, so a change and its audit entry commit together.
Events that are not row changes use :func:`record`.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from costlens.core.logging import request_id_var
from costlens.db.tenant import current_tenant
from costlens.models.audit import AuditEvent

REDACTED = "[redacted]"
_INFO_KEY = "costlens.audit_actor"
SKIP_COLUMNS = frozenset({"created_at", "updated_at"})


@dataclass(frozen=True)
class Actor:
    type: str = "system"
    user_id: uuid.UUID | None = None
    label: str = "system"
    ip: str | None = None
    user_agent: str | None = None


def set_actor(session: Session, actor: Actor) -> None:
    session.info[_INFO_KEY] = actor


def current_actor(session: Session) -> Actor:
    return session.info.get(_INFO_KEY) or Actor()


def _plain(value: Any) -> Any:
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, (uuid.UUID, datetime, date)):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return str(value)


def singular(table: str) -> str:
    """Table name -> action noun: ``cost_anomalies`` -> ``cost_anomaly``."""
    if table.endswith("ies"):
        return table[:-3] + "y"
    if table.endswith("ss"):  # elevated_access
        return table
    return table.removesuffix("s")


def _org_of(obj: Any) -> uuid.UUID | None:
    if getattr(obj, "__tablename__", None) == "organizations":
        return obj.id
    return getattr(obj, "org_id", None)


def _label(obj: Any) -> str:
    for attr in ("display_name", "name", "email"):
        value = getattr(obj, attr, None)
        if isinstance(value, str) and value:
            return value
    return ""


def record(
    session: Session,
    *,
    org_id: uuid.UUID,
    action: str,
    target_type: str,
    target_id: Any = None,
    summary: str = "",
    changes: dict[str, Any] | None = None,
) -> AuditEvent:
    actor = current_actor(session)
    entry = AuditEvent(
        org_id=org_id,
        actor_type=actor.type,
        actor_user_id=actor.user_id,
        actor_label=actor.label[:320],
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        summary=summary[:500],
        changes=changes or {},
        ip=actor.ip,
        user_agent=(actor.user_agent or "")[:500] or None,
        request_id=request_id_var.get(),
    )
    session.add(entry)
    return entry


def _diff(obj: Any, *, mode: str) -> dict[str, list[Any]]:
    redact = set(getattr(type(obj), "__audit_redact__", ()))
    # Columns that change on their own (heartbeats, counters): never recorded.
    ignore = set(getattr(type(obj), "__audit_ignore__", ()))
    state = inspect(obj)
    changes: dict[str, list[Any]] = {}
    for attr in state.mapper.column_attrs:
        key = attr.key
        if key in SKIP_COLUMNS or key in ignore or key == "id":
            continue
        hist = state.attrs[key].history
        if mode == "insert":
            value = getattr(obj, key)
            if value is None:
                continue
            changes[key] = [None, REDACTED if key in redact else _plain(value)]
        elif mode == "delete":
            value = getattr(obj, key)
            changes[key] = [REDACTED if key in redact else _plain(value), None]
        elif hist.has_changes():
            before = hist.deleted[0] if hist.deleted else None
            after = hist.added[0] if hist.added else None
            if key in redact:
                changes[key] = [REDACTED, REDACTED]
            elif _plain(before) != _plain(after):
                changes[key] = [_plain(before), _plain(after)]
    return changes


@event.listens_for(Session, "before_flush")
def _capture(session: Session, _ctx: Any, _instances: Any) -> None:
    ctx = current_tenant(session)
    pending: list[tuple[Any, str]] = []
    pending += [(o, "insert") for o in session.new if getattr(o, "__audited__", False)]
    pending += [(o, "update") for o in session.dirty if getattr(o, "__audited__", False)]
    pending += [(o, "delete") for o in session.deleted if getattr(o, "__audited__", False)]
    for obj, mode in pending:
        if mode == "update" and not session.is_modified(obj, include_collections=False):
            continue
        if mode == "insert" and getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        org_id = _org_of(obj)
        if org_id is None or (not ctx.bypass and ctx.org_id != org_id):
            continue  # recorded explicitly by the service (e.g. organisation created)
        changes = _diff(obj, mode=mode)
        if mode == "update" and not changes:
            continue
        table = obj.__tablename__
        verb = {"insert": "created", "update": "updated", "delete": "deleted"}[mode]
        noun = singular(table)
        record(
            session,
            org_id=org_id,
            action=f"{noun}.{verb}",
            target_type=noun,
            target_id=obj.id,
            summary=f"{noun.replace('_', ' ')} {verb}"
            + (f": {_label(obj)}" if _label(obj) else ""),
            changes=changes,
        )
