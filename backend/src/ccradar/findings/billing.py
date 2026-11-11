"""Monthly cost per resource split by usage type, from resource-level bill lines."""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ccradar.costs import fx
from ccradar.db.base import Base

MONTH = 30.4


def usage_costs(
    db: Session,
    org_id: uuid.UUID,
    currency: str,
    resource_ids: list[str],
    classify: Callable[[str | None], str],
    days: int = 30,
) -> dict[str, dict[str, float]]:
    """{resource id: {class: monthly cost}} over the last ``days`` of the bill."""
    if not resource_ids:
        return {}
    t = Base.metadata.tables["cost_line_items"]
    last = db.scalar(select(func.max(t.c.charge_date)).where(t.c.org_id == org_id))
    if last is None:
        return {}
    amount = fx.converted(
        t.c.effective_cost, t.c.billing_currency, t.c.charge_date, currency, t.c.org_id
    )
    out: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for start in range(0, len(resource_ids), 500):
        for rid, usage, total in db.execute(
            select(t.c.resource_id, t.c.usage_type, func.sum(amount))
            .where(
                t.c.org_id == org_id,
                t.c.resource_id.in_(resource_ids[start : start + 500]),
                t.c.charge_date > last - timedelta(days=days),
            )
            .group_by(t.c.resource_id, t.c.usage_type)
        ):
            out[rid][classify(usage)] += float(total or 0) * MONTH / days
    return out


def usage_type_costs(
    db: Session, org_id: uuid.UUID, currency: str, patterns: dict[str, str], days: int = 30
) -> dict[str, float]:
    """Monthly cost of all bill lines whose usage type (or service, for ``service:`` patterns)
    matches each SQL LIKE pattern, keyed like ``patterns``."""
    t = Base.metadata.tables["cost_line_items"]
    last = db.scalar(select(func.max(t.c.charge_date)).where(t.c.org_id == org_id))
    if last is None:
        return dict.fromkeys(patterns, 0.0)
    amount = fx.converted(
        t.c.effective_cost, t.c.billing_currency, t.c.charge_date, currency, t.c.org_id
    )
    out = {}
    for key, pattern in patterns.items():
        column, like = (
            (t.c.service_name, pattern.removeprefix("service:"))
            if pattern.startswith("service:")
            else (t.c.usage_type, pattern)
        )
        total = db.scalar(
            select(func.sum(amount)).where(
                t.c.org_id == org_id,
                column.ilike(like),
                t.c.charge_date > last - timedelta(days=days),
            )
        )
        out[key] = float(total or 0) * MONTH / days
    return out
