"""Declarative base and common column helpers."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, MetaData, Uuid, event
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDPrimaryKey:
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


def _utc_on_load(target: object, *_: object) -> None:
    """SQLite stores times without a zone and returns them naive; everything CloudCostRadar
    stores is UTC. Mark loaded times as UTC so the API always sends a zone (otherwise a
    browser reads "10:00" as local time and shows ages that are hours off). PostgreSQL
    already returns zoned times, so this does nothing there."""
    state = sa_inspect(target)
    for name in _datetime_attrs(type(target)):
        value = state.dict.get(name)
        if isinstance(value, datetime) and value.tzinfo is None:
            state.dict[name] = value.replace(tzinfo=UTC)


_DATETIME_ATTRS: dict[type, tuple[str, ...]] = {}


def _datetime_attrs(cls: type) -> tuple[str, ...]:
    found = _DATETIME_ATTRS.get(cls)
    if found is None:
        mapper = sa_inspect(cls)
        found = tuple(
            attr.key
            for attr in mapper.column_attrs
            if any(isinstance(col.type, DateTime) for col in attr.columns)
        )
        _DATETIME_ATTRS[cls] = found
    return found


event.listen(Base, "load", _utc_on_load, propagate=True)
event.listen(Base, "refresh", _utc_on_load, propagate=True)
