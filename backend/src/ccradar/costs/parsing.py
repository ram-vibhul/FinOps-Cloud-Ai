"""Forgiving parsers for values found in billing files."""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

_NUMBER_JUNK = re.compile(r"[\s,$€£₹¥]")


class ValueProblem(ValueError):
    pass


def parse_decimal(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal(0)
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = str(value).strip()
    negative = text.startswith("(") and text.endswith(")")
    text = _NUMBER_JUNK.sub("", text.strip("()"))
    try:
        number = Decimal(text)
    except InvalidOperation:
        raise ValueProblem(f"not a number: {value!r}") from None
    if not number.is_finite():
        raise ValueProblem(f"not a number: {value!r}")
    return -number if negative else number


def parse_date(value: Any, fmt: str | None = None) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        raise ValueProblem("date is empty")
    try:
        if fmt:
            return datetime.strptime(text, fmt).date()
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        hint = f" (expected {fmt})" if fmt else " (use YYYY-MM-DD or set a date format)"
        raise ValueProblem(f"not a date: {value!r}{hint}") from None


def parse_tags(value: Any) -> dict[str, str]:
    """FOCUS Tags: a JSON object (string or mapping)."""
    if not value:
        return {}
    if isinstance(value, dict):
        data = value
    elif isinstance(value, list):
        data = dict(value)
    else:
        try:
            data = json.loads(str(value))
        except json.JSONDecodeError:
            raise ValueProblem("tags are not a JSON object") from None
        if not isinstance(data, dict):
            raise ValueProblem("tags are not a JSON object")
    return {str(k): str(v) for k, v in data.items() if v not in (None, "")}
