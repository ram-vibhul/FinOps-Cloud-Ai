"""Ask in plain words: the AI turns a sentence into a report-builder query.

The model only fills in a small JSON object (structured output). That object is then
checked against ``ExploreQuery`` like any hand-made query, and returned for the person
to look at before it runs. No SQL is ever produced, and no cost figures go to the AI:
only the sentence, the dimension names, tag keys and the names of services and regions.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from costlens.ai.writer import StructuredWriter
from costlens.core.errors import AppError
from costlens.costs import explorer
from costlens.costs.analytics import latest_date
from costlens.db.base import utcnow

FIXED_DIMENSIONS = [*explorer.LINE_DIMENSIONS, "team", "project"]
CONTEXT_DAYS = 90

SYSTEM = (
    "You turn a question about cloud costs into a query for a cost report builder. "
    "Fill in the JSON fields only. Use only the dimensions, tag keys and values listed. "
    "Dimensions are written as listed, tags as 'tag:<key>'. Use 'days' for ranges like "
    "'last 3 months' (about 30 days a month) and leave start and end empty; use start "
    "and end (YYYY-MM-DD) only for exact dates. Granularity is 'month' for ranges over "
    "three months, 'day' for shorter ones, 'total' when only a total is asked for. "
    "If the question is not about costs, set understood to false. 'explanation' says in "
    "one short sentence what the report will show."
)

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "understood": {"type": "boolean"},
        "explanation": {"type": "string"},
        "days": {"type": ["integer", "null"]},
        "start": {"type": ["string", "null"]},
        "end": {"type": ["string", "null"]},
        "metric": {"type": "string", "enum": ["effective", "billed"]},
        "granularity": {"type": "string", "enum": ["total", "day", "month"]},
        "group_by": {"type": "array", "items": {"type": "string"}},
        "filters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "dimension": {"type": "string"},
                    "values": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["dimension", "values"],
                "additionalProperties": False,
            },
        },
        "chart": {"type": "string", "enum": ["bar", "line", "table"]},
    },
    "required": [
        "understood",
        "explanation",
        "days",
        "start",
        "end",
        "metric",
        "granularity",
        "group_by",
        "filters",
        "chart",
    ],
    "additionalProperties": False,
}


def context(db: Session, org_id: uuid.UUID, end: date) -> str:
    start = end - timedelta(days=CONTEXT_DAYS - 1)
    tags = [t["key"] for t in explorer.tag_keys(db, org_id, start=start, end=end, limit=50)]
    lines = [f"Latest day with cost data: {end.isoformat()}"]
    lines.append("Dimensions: " + ", ".join(FIXED_DIMENSIONS))
    lines.append("Tag keys: " + (", ".join(tags) or "none"))
    for dim in ("service", "region", "charge_category"):
        values = explorer.dimension_values(db, org_id, dim, start=start, end=end, limit=60)
        lines.append(f"{dim} values: " + ", ".join(v["value"] for v in values))
    return "\n".join(lines)


def _query(raw: dict[str, Any], end: date) -> explorer.ExploreQuery:
    if raw.get("start") and raw.get("end"):
        start, stop = date.fromisoformat(raw["start"]), date.fromisoformat(raw["end"])
    else:
        days = max(1, min(int(raw.get("days") or 30), 400))
        start, stop = end - timedelta(days=days - 1), end
    filters: dict[str, list[str]] = {}
    for f in raw.get("filters") or []:
        values = [str(v) for v in f.get("values") or [] if str(v).strip()]
        if values:
            filters.setdefault(str(f["dimension"]), []).extend(values)
    return explorer.ExploreQuery(
        start=start,
        end=stop,
        metric=raw.get("metric") or "effective",
        granularity=raw.get("granularity") or "day",
        group_by=[str(g) for g in raw.get("group_by") or ["service"]][:2] or ["service"],
        filters=filters,
    )


def ask(db: Session, org_id: uuid.UUID, sentence: str, writer: StructuredWriter) -> dict[str, Any]:
    end = latest_date(db, org_id) or utcnow().date()
    prompt = f"{context(db, org_id, end)}\n\nQuestion: {sentence.strip()}"
    raw = writer.structured(prompt, SYSTEM, SCHEMA)
    if raw is None:
        raise AppError("The AI could not answer just now. Try again.", code="ai_failed")
    if not raw.get("understood", True):
        raise AppError(
            "That does not look like a question about costs. Try something like "
            "'EC2 cost by region for 3 months'.",
            code="ai_not_understood",
        )
    try:
        query = _query(raw, end)
    except (ValidationError, ValueError, KeyError, TypeError) as exc:
        raise AppError(
            "The AI suggested a report this app cannot run. Try rewording the question.",
            code="ai_bad_query",
        ) from exc
    if query.start > query.end:
        raise AppError(
            "The AI suggested a date range that ends before it starts.", code="ai_bad_query"
        )
    chart = raw.get("chart") if raw.get("chart") in ("bar", "line", "table") else "bar"
    return {
        "query": query.model_dump(mode="json", exclude={"currency", "top", "limit"}),
        "chart": chart,
        "explanation": str(raw.get("explanation") or "")[:300],
    }
