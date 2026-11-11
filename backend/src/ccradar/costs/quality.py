"""Data quality: tag coverage, unallocated spend, inconsistent tag keys and values."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func, literal, select
from sqlalchemy.orm import Session

from ccradar.costs.explorer import TAG_KEY, tag_aliases
from ccradar.costs.fx import converted
from ccradar.costs.tagrules import canonical
from ccradar.db.base import Base
from ccradar.models import Organization

USAGE = ("Usage", "Adjustment")  # taxes, credits and purchases cannot be tagged
MAX_KEYS_FOR_VALUES = 20


class QualitySettings(BaseModel):
    required_tags: list[str] = Field(
        default_factory=lambda: ["team", "project", "env"], max_length=20
    )


def quality_settings(org: Organization | None) -> QualitySettings:
    raw = ((org.settings if org else None) or {}).get("data_quality", {})
    return QualitySettings.model_validate(raw)


def _base(table: Any, org_id: uuid.UUID, start: date, end: date) -> tuple[Any, ...]:
    return (
        table.c.org_id == org_id,
        table.c.charge_date >= start,
        table.c.charge_date <= end,
        table.c.charge_category.in_(USAGE),
    )


def _key_spend(db: Session, table: Any, base: tuple[Any, ...], cost: Any) -> dict[str, float]:
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        keys = func.json_object_keys(table.c.tags).table_valued("value").render_derived()
        key_col = keys.c.value
    else:
        keys = func.json_each(table.c.tags).table_valued("key")
        key_col = keys.c.key
    rows = db.execute(
        select(key_col, func.sum(cost))
        .select_from(table)
        .join(keys, literal(True))
        .where(*base)
        .group_by(key_col)
    ).all()
    return {k: float(v or 0) for k, v in rows}


def report(
    db: Session, org_id: uuid.UUID, start: date, end: date, currency: str | None = None
) -> dict[str, Any]:
    table = Base.metadata.tables["cost_line_items"]
    org = db.get(Organization, org_id)
    currency = currency or (org.default_currency if org else "USD")
    base = _base(table, org_id, start, end)
    cost = converted(
        table.c.effective_cost,
        table.c.billing_currency,
        table.c.charge_date,
        currency,
        table.c.org_id,
    )
    total = float(db.scalar(select(func.sum(cost)).where(*base)) or 0)
    settings = quality_settings(org)
    team_tag = tag_aliases(org)["team"]

    coverage = []
    for key in settings.required_tags:
        tag = table.c.tags[key].as_string()
        untagged = float(db.scalar(select(func.sum(cost)).where(*base, tag.is_(None))) or 0)
        top = db.execute(
            select(table.c.service_name, func.sum(cost))
            .where(*base, tag.is_(None))
            .group_by(table.c.service_name)
            .order_by(func.sum(cost).desc())
            .limit(5)
        ).all()
        coverage.append(
            {
                "key": key,
                "untagged_cost": round(untagged, 2),
                "coverage_pct": round((total - untagged) / total * 100, 1) if total else 100.0,
                "top_untagged_services": [
                    {"service": s, "cost": round(float(c or 0), 2)} for s, c in top
                ],
            }
        )

    team = (
        table.c.vtags[team_tag[5:]] if team_tag.startswith("vtag:") else table.c.tags[team_tag]
    ).as_string()
    unallocated = float(db.scalar(select(func.sum(cost)).where(*base, team.is_(None))) or 0)
    untagged_resources = db.execute(
        select(table.c.resource_id, func.max(table.c.service_name), func.sum(cost))
        .where(*base, team.is_(None), table.c.resource_id.is_not(None))
        .group_by(table.c.resource_id)
        .order_by(func.sum(cost).desc())
        .limit(10)
    ).all()

    key_spend = _key_spend(db, table, base, cost)
    key_groups: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for key, spend in key_spend.items():
        key_groups[canonical(key)].append((key, spend))
    inconsistent_keys = [
        {
            "canonical": c,
            "suggested": max(v, key=lambda x: x[1])[0],
            "variants": [
                {"key": k, "cost": round(s, 2)} for k, s in sorted(v, key=lambda x: -x[1])
            ],
        }
        for c, v in key_groups.items()
        if len(v) > 1
    ]

    inconsistent_values = []
    top_keys = sorted(key_spend, key=lambda k: -key_spend[k])[:MAX_KEYS_FOR_VALUES]
    for key in top_keys:
        if not TAG_KEY.match(key):
            continue
        tag = table.c.tags[key].as_string()
        rows = db.execute(
            select(tag, func.sum(cost)).where(*base, tag.is_not(None)).group_by(tag)
        ).all()
        groups: dict[str, list[tuple[str, float]]] = defaultdict(list)
        for value, spend in rows:
            groups[canonical(str(value))].append((str(value), float(spend or 0)))
        for c, variants in groups.items():
            if len(variants) > 1:
                inconsistent_values.append(
                    {
                        "key": key,
                        "canonical": c,
                        "suggested": max(variants, key=lambda x: x[1])[0],
                        "variants": [
                            {"value": v, "cost": round(s, 2)}
                            for v, s in sorted(variants, key=lambda x: -x[1])
                        ],
                    }
                )

    return {
        "currency": currency,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "taggable_cost": round(total, 2),
        "required_tags": settings.required_tags,
        "coverage": coverage,
        "unallocated": {
            "tag": team_tag,
            "cost": round(unallocated, 2),
            "pct": round(unallocated / total * 100, 1) if total else 0.0,
        },
        "top_unallocated_resources": [
            {"resource_id": r, "service": s, "cost": round(float(c or 0), 2)}
            for r, s, c in untagged_resources
        ],
        "inconsistent_keys": inconsistent_keys,
        "inconsistent_values": inconsistent_values,
    }
