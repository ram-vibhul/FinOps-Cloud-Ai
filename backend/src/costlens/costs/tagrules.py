"""Tag normalisation rules: merge spelling variants of tag keys and values.

Rules live in ``organizations.settings.tag_rules``:

* ``{"kind": "key", "from": ["Team", "TEAM"], "to": "team"}``
* ``{"kind": "value", "key": "team", "from": ["Payments"], "to": "payments"}``

They are applied when costs are imported and can be re-applied to existing data.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from costlens.costs.ingest import refresh_daily
from costlens.db.base import Base
from costlens.models import Organization

_SPLIT = re.compile(r"[\s_\-.:/]+|(?<=[a-z0-9])(?=[A-Z])")


def canonical(text: str) -> str:
    """Comparison form: ``CostCenter``, ``cost-center`` and ``Cost Center`` are equal."""
    return "".join(part.lower() for part in _SPLIT.split(text.strip()) if part)


class TagRule(BaseModel):
    kind: Literal["key", "value"]
    key: str | None = Field(default=None, max_length=128)  # for value rules
    from_: list[str] = Field(alias="from", min_length=1, max_length=50)
    to: str = Field(min_length=1, max_length=256)

    model_config = {"populate_by_name": True}


def org_rules(org: Organization | None) -> list[TagRule]:
    raw = ((org.settings if org else None) or {}).get("tag_rules", [])
    return [TagRule.model_validate(r) for r in raw]


def apply_rules(tags: dict[str, str], rules: list[TagRule]) -> dict[str, str]:
    if not rules or not tags:
        return tags
    out = dict(tags)
    for rule in rules:
        if rule.kind == "key":
            for variant in rule.from_:
                if variant in out and variant != rule.to:
                    value = out.pop(variant)
                    out.setdefault(rule.to, value)
    for rule in rules:
        if rule.kind == "value" and rule.key in out:
            current = out[rule.key]
            if current in rule.from_ or current.strip() in rule.from_:
                out[rule.key] = rule.to
    return out


def reapply(db: Session, org_id: uuid.UUID, batch: int = 2000) -> int:
    """Rewrite tags of existing line items with the current rules; refresh nothing that
    is not tag-based (the daily rollup has no tags), so only line items change."""
    org = db.get(Organization, org_id)
    rules = org_rules(org)
    if not rules:
        return 0
    table = Base.metadata.tables["cost_line_items"]
    changed = 0
    last: tuple[Any, Any] | None = None
    while True:
        stmt = (
            select(table.c.id, table.c.charge_date, table.c.tags)
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
        for row_id, day, tags in rows:
            new = apply_rules(tags or {}, rules)
            if new != (tags or {}):
                db.execute(
                    update(table)
                    .where(table.c.id == row_id, table.c.charge_date == day)
                    .values(tags=new)
                )
                changed += 1
        last = (rows[-1][1], rows[-1][0])
    db.flush()
    return changed


__all__ = ["refresh_daily"]
