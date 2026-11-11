"""PromQL checks the agent repeats before running a customer query.

KEEP IN SYNC with backend/src/ccradar/k8s/promql.py: a backend test checks that the
rules are identical.
"""

from __future__ import annotations

import re

MAX_LENGTH = 500
MAX_RANGE_SECONDS = 86_400  # 1 day
_RANGE = re.compile(r"\[([^\]]*)\]")
_DURATION = re.compile(r"(\d+)(ms|s|m|h|d|w|y)")
_UNITS = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86_400, "w": 604_800, "y": 31_536_000}


def _seconds(text: str) -> float | None:
    if not re.fullmatch(r"(\d+(ms|s|m|h|d|w|y))+", text):
        return None
    return sum(int(n) * _UNITS[u] for n, u in _DURATION.findall(text))


def _shape(promql: str) -> str | None:
    if not promql or not promql.strip():
        return "the query is empty"
    if len(promql) > MAX_LENGTH:
        return f"the query is longer than {MAX_LENGTH} characters"
    if any(ch in promql for ch in "\n\r\t") or not promql.isprintable() or not promql.isascii():
        return "use a single line of plain characters"
    return None


def _brackets(promql: str) -> str | None:
    stack: list[str] = []
    pairs = {")": "(", "}": "{", "]": "["}
    quote: str | None = None
    for ch in promql:
        if quote:
            quote = None if ch == quote else quote
        elif ch in "\"'`":
            quote = ch
        elif ch in "({[":
            stack.append(ch)
        elif ch in ")}]" and (not stack or stack.pop() != pairs[ch]):
            return "brackets do not match"
    if quote:
        return "a quote is not closed"
    return "brackets do not match" if stack else None


def _ranges(promql: str) -> str | None:
    unquoted = re.sub(r"\"[^\"]*\"|'[^']*'", "", promql)
    for window in _RANGE.findall(unquoted):
        for part in (p.strip() for p in window.split(":") if p.strip()):
            seconds = _seconds(part)
            if seconds is None:
                return f"'{window}' is not a valid range"
            if seconds > MAX_RANGE_SECONDS:
                return "range windows can be at most 1d"
    return None


def problem(promql: str) -> str | None:
    """None when the query is acceptable, else a short reason."""
    return _shape(promql) or _brackets(promql) or _ranges(promql)
