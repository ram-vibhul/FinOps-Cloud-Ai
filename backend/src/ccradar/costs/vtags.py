"""Virtual tags: rule-based tags computed for every cost line.

A definition has a ``key``, ordered ``rules`` (first match wins), an optional
``default_value`` and ``override_existing``. Each rule::

    {"value": "payments",
     "match": {"account": ["2222..."], "service": ["Amazon RDS"], "region": ["ap-south-1"],
               "tag": {"project": ["checkout"]}, "resource_contains": ["checkout"],
               "usage_type_contains": ["BoxUsage"]}}

All conditions in one rule must hold (AND); values in a list are alternatives (OR).
If the line already has a real tag with the same key, that value wins unless
``override_existing``. Results are stored in ``cost_line_items.vtags`` so the original
tags are never changed.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ccradar.costs.explorer import TAG_KEY
from ccradar.db.base import Base
from ccradar.models import VirtualTag

FIELDS = {
    "account": "sub_account_id",
    "service": "service_name",
    "region": "region",
    "provider": "provider",
    "charge_category": "charge_category",
}


class Match(BaseModel):
    account: list[str] = Field(default_factory=list, max_length=500)
    service: list[str] = Field(default_factory=list, max_length=500)
    region: list[str] = Field(default_factory=list, max_length=100)
    provider: list[str] = Field(default_factory=list, max_length=20)
    charge_category: list[str] = Field(default_factory=list, max_length=10)
    tag: dict[str, list[str]] = Field(default_factory=dict)
    resource_contains: list[str] = Field(default_factory=list, max_length=100)
    usage_type_contains: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("tag")
    @classmethod
    def _keys(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        for key in value:
            if not TAG_KEY.match(key):
                raise ValueError(f"not a valid tag key: {key!r}")
        return value

    def empty(self) -> bool:
        return not any(getattr(self, f) for f in type(self).model_fields)


class Rule(BaseModel):
    value: str = Field(min_length=1, max_length=256)
    match: Match


class Definition(BaseModel):
    key: str = Field(min_length=1, max_length=128, pattern=r"^[\w.:/@+=\- ]{1,128}$")
    description: str = Field(default="", max_length=500)
    default_value: str | None = Field(default=None, max_length=256)
    override_existing: bool = False
    rules: list[Rule] = Field(default_factory=list, max_length=500)


def matches(rule: Rule, line: dict[str, Any]) -> bool:
    m = rule.match
    if m.empty():
        return False
    for name, column in FIELDS.items():
        wanted = getattr(m, name)
        if wanted and line.get(column) not in wanted:
            return False
    tags = line.get("tags") or {}
    for key, values in m.tag.items():
        if tags.get(key) not in values:
            return False
    if m.resource_contains:
        haystack = f"{line.get('resource_id') or ''} {line.get('resource_name') or ''}".lower()
        if not any(s.lower() in haystack for s in m.resource_contains):
            return False
    if m.usage_type_contains:
        usage = (line.get("usage_type") or "").lower()
        if not any(s.lower() in usage for s in m.usage_type_contains):
            return False
    return True


def compute(definitions: list[Definition], line: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    tags = line.get("tags") or {}
    for d in definitions:
        if not d.override_existing and tags.get(d.key):
            out[d.key] = tags[d.key]
            continue
        value = next((r.value for r in d.rules if matches(r, line)), None)
        if value is None:
            value = tags.get(d.key) or d.default_value
        if value:
            out[d.key] = value
    return out


def load(db: Session, org_id: uuid.UUID) -> list[Definition]:
    rows = db.scalars(
        select(VirtualTag).where(VirtualTag.org_id == org_id).order_by(VirtualTag.key)
    ).all()
    return [
        Definition(
            key=r.key,
            description=r.description,
            default_value=r.default_value,
            override_existing=r.override_existing,
            rules=[Rule.model_validate(x) for x in r.rules],
        )
        for r in rows
    ]


LINE_COLUMNS = (
    "sub_account_id",
    "service_name",
    "region",
    "provider",
    "charge_category",
    "resource_id",
    "resource_name",
    "usage_type",
    "tags",
)


def recompute(db: Session, org_id: uuid.UUID, batch: int = 2000) -> int:
    """Recalculate ``vtags`` for every line of the organisation (keyset batches)."""
    definitions = load(db, org_id)
    table = Base.metadata.tables["cost_line_items"]
    cols = [table.c[c] for c in LINE_COLUMNS]
    changed, last = 0, None
    while True:
        stmt = (
            select(table.c.id, table.c.charge_date, table.c.vtags, *cols)
            .where(table.c.org_id == org_id)
            .order_by(table.c.charge_date, table.c.id)
            .limit(batch)
        )
        if last is not None:
            stmt = stmt.where(
                (table.c.charge_date > last[0])
                | ((table.c.charge_date == last[0]) & (table.c.id > last[1]))
            )
        rows = db.execute(stmt).all()
        if not rows:
            break
        for raw in rows:
            row = tuple(raw)
            line: dict[str, Any] = dict(zip(LINE_COLUMNS, row[3:], strict=True))
            new = compute(definitions, line)
            if new != (row[2] or {}):
                db.execute(
                    update(table)
                    .where(table.c.id == row[0], table.c.charge_date == row[1])
                    .values(vtags=new)
                )
                changed += 1
        tail = tuple(rows[-1])
        last = (tail[1], tail[0])
    db.flush()
    return changed


def preview(
    db: Session,
    org_id: uuid.UUID,
    definition: Definition,
    *,
    start: Any,
    end: Any,
    limit_rows: int = 200_000,
) -> dict[str, Any]:
    """Spend per resulting value over a range, without saving anything."""
    table = Base.metadata.tables["cost_line_items"]
    cols = [table.c[c] for c in LINE_COLUMNS]
    rows = db.execute(
        select(table.c.effective_cost, table.c.billing_currency, *cols)
        .where(table.c.org_id == org_id, table.c.charge_date >= start, table.c.charge_date <= end)
        .limit(limit_rows)
    ).all()
    totals: dict[str, float] = {}
    source: dict[str, int] = {"tag": 0, "rule": 0, "default": 0, "none": 0}
    for raw in rows:
        row = tuple(raw)
        line: dict[str, Any] = dict(zip(LINE_COLUMNS, row[2:], strict=True))
        tags = line.get("tags") or {}
        value = compute([definition], line).get(definition.key)
        if tags.get(definition.key) and not definition.override_existing:
            source["tag"] += 1
        elif value and value != definition.default_value:
            source["rule"] += 1
        elif value:
            source["default"] += 1
        else:
            source["none"] += 1
        label = value or "(no value)"
        totals[label] = totals.get(label, 0.0) + float(row[0] or 0)
    return {
        "values": [
            {"value": k, "cost": round(v, 2)}
            for k, v in sorted(totals.items(), key=lambda kv: -kv[1])
        ],
        "lines_by_source": source,
        "lines": len(rows),
        "truncated": len(rows) >= limit_rows,
    }
