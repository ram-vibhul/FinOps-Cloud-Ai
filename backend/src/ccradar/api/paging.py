"""Cursor paging for list APIs that grow (11.17).

Lists stay plain JSON arrays, so existing clients keep working; when more rows exist
the response carries an ``X-Next-Cursor`` header, and passing it back as ``?cursor=``
returns the next page. Cursors are keyset positions (the sort values of the last row,
newest first, ending in the row id), so pages do not skip or repeat rows when new ones
arrive between requests, and a deep page costs the same as the first."""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Callable, Sequence
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import Query, Response
from sqlalchemy import Select, tuple_
from sqlalchemy.orm import InstrumentedAttribute

from ccradar.core.errors import AppError

HEADER = "X-Next-Cursor"
Cursor = Annotated[str | None, Query(max_length=500, description="From X-Next-Cursor.")]


def _pack(value: Any) -> Any:
    if isinstance(value, datetime):
        return {"dt": value.isoformat()}
    if isinstance(value, date):
        return {"d": value.isoformat()}
    if isinstance(value, uuid.UUID):
        return {"u": str(value)}
    if value is None or isinstance(value, bool):
        raise TypeError("cursor columns must not be empty or yes/no")
    return value


def _unpack(value: Any) -> Any:
    if isinstance(value, dict) and len(value) == 1:
        ((kind, raw),) = value.items()
        return {"dt": datetime.fromisoformat, "d": date.fromisoformat, "u": uuid.UUID}[kind](raw)
    if isinstance(value, int | float | str):
        return value
    raise ValueError("bad cursor value")


def encode(values: Sequence[Any]) -> str:
    raw = json.dumps([_pack(v) for v in values], separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode(cursor: str, size: int) -> list[Any]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        values = [_unpack(v) for v in json.loads(base64.urlsafe_b64decode(padded))]
    except (ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
        raise AppError("Invalid cursor.", code="invalid_cursor") from exc
    if len(values) != size:
        raise AppError("Invalid cursor.", code="invalid_cursor")
    return values


def keyset[*Ts](
    stmt: Select[*Ts], cursor: str | None, *columns: InstrumentedAttribute[Any]
) -> Select[*Ts]:
    """Order ``stmt`` by ``columns``, newest/largest first (the last must be unique,
    normally the id), and start after ``cursor``."""
    stmt = stmt.order_by(*(c.desc() for c in columns))
    if cursor:
        stmt = stmt.where(tuple_(*columns) < tuple_(*decode(cursor, len(columns))))
    return stmt


def page[T](
    rows: Sequence[T],
    limit: int,
    response: Response,
    *columns: InstrumentedAttribute[Any],
    of: Callable[[T], Any] = lambda row: row,
) -> list[T]:
    """The first ``limit`` of ``rows`` (fetched with ``limit + 1``); sets the next cursor
    header when there are more. ``columns`` are the ones given to :func:`keyset`; ``of``
    picks the model out of a result row that holds more than one."""
    out = list(rows[:limit])
    if len(rows) > limit:
        last = of(out[-1])
        response.headers[HEADER] = encode([getattr(last, c.key) for c in columns])
    return out
