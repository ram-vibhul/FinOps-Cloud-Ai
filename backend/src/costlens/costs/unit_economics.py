"""Cost per unit: the cost a metric's filters select, divided by the metric each day,
with monthly figures and the change against the previous period."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from costlens.costs.budgets import daily_spend
from costlens.costs.explorer import _resolve, tag_aliases
from costlens.models import Organization, UnitMetric, UnitMetricValue


def upsert_values(
    db: Session, org_id: uuid.UUID, metric: UnitMetric, values: list[tuple[date, float]]
) -> int:
    existing = {
        v.day: v
        for v in db.scalars(select(UnitMetricValue).where(UnitMetricValue.metric_id == metric.id))
    }
    for day, value in values:
        row = existing.get(day)
        if row is None:
            db.add(UnitMetricValue(org_id=org_id, metric_id=metric.id, day=day, value=value))
        else:
            row.value = value
    db.flush()
    return len(values)


def report(
    db: Session, org_id: uuid.UUID, metric: UnitMetric, start: date, end: date
) -> dict[str, Any]:
    org = db.get(Organization, org_id)
    currency = org.default_currency if org else "USD"
    filters = {_resolve(k, tag_aliases(org)): v for k, v in (metric.filters or {}).items()}
    cost = daily_spend(db, org_id, filters, start, end, metric.metric, currency)
    units = {
        v.day: v.value
        for v in db.scalars(
            select(UnitMetricValue).where(
                UnitMetricValue.metric_id == metric.id,
                UnitMetricValue.day >= start,
                UnitMetricValue.day <= end,
            )
        )
    }
    days = []
    monthly: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    d = start
    while d <= end:
        c, u = cost.get(d, 0.0), units.get(d)
        days.append(
            {
                "date": d.isoformat(),
                "cost": round(c, 2),
                "units": u,
                "per_unit": round(c / u, 6) if u else None,
            }
        )
        if u:
            m = monthly[d.strftime("%Y-%m")]
            m[0] += c
            m[1] += u
        d += timedelta(days=1)
    months = [
        {
            "month": k,
            "cost": round(v[0], 2),
            "units": v[1],
            "per_unit": round(v[0] / v[1], 6) if v[1] else None,
        }
        for k, v in sorted(monthly.items())
    ]
    covered = [x for x in days if x["units"]]
    total_cost = sum(x["cost"] for x in covered)
    total_units = sum(x["units"] or 0 for x in covered)
    half = len(covered) // 2
    first, second = covered[:half], covered[half:]

    def per(rows: list[dict[str, Any]]) -> float | None:
        u = sum(r["units"] or 0 for r in rows)
        return sum(r["cost"] for r in rows) / u if u else None

    a, b = per(first), per(second)
    return {
        "metric": {
            "id": str(metric.id),
            "name": metric.name,
            "unit": metric.unit,
            "target_per_unit": metric.target_per_unit,
        },
        "currency": currency,
        "per_unit": round(total_cost / total_units, 6) if total_units else None,
        "change_pct": round(100 * (b - a) / a, 1) if a and b is not None else None,
        "days_with_units": len(covered),
        "days": days,
        "months": months,
    }
