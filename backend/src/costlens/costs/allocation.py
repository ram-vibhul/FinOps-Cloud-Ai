"""Showback and chargeback: each value of a dimension (e.g. each team) gets its direct
cost plus its share of the shared pools, and the parts always add up to the bill to the
cent (largest-remainder rounding).

Every cost line goes to the first rule (lowest position) whose filters it matches;
otherwise it is a direct cost of its own value (or "(untagged)")."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from costlens.core.errors import AppError
from costlens.costs.explorer import UNTAGGED, _expr, _label, _needs_lines, _resolve, tag_aliases
from costlens.costs.fx import converted
from costlens.db.base import Base
from costlens.models import AllocationRule, Organization

CENT = Decimal("0.01")


def split_cents(total: Decimal, weights: dict[str, float]) -> dict[str, Decimal]:
    """Split ``total`` across keys by weight; the parts are whole cents summing exactly to it."""
    cents = int((total / CENT).to_integral_value(ROUND_HALF_UP))
    if not weights:
        return {}
    keys = sorted(weights)
    wsum = sum(max(weights[k], 0.0) for k in keys)
    shares = {k: (max(weights[k], 0.0) / wsum if wsum else 1 / len(keys)) for k in keys}
    exact = {k: cents * shares[k] for k in keys}
    floor = {k: int(exact[k]) if exact[k] >= 0 else -int(-exact[k]) for k in keys}
    left = cents - sum(floor.values())
    order = sorted(
        keys, key=lambda k: (-(exact[k] - floor[k]) if left >= 0 else exact[k] - floor[k], k)
    )
    for k in order[: abs(left)]:
        floor[k] += 1 if left > 0 else -1
    return {k: Decimal(floor[k]) * CENT for k in keys}


def _matches(filters: dict[str, list[str]], row: dict[str, str]) -> bool:
    return all(row.get(dim) in values for dim, values in filters.items())


def showback(
    db: Session, org_id: uuid.UUID, month: date, dimension: str, metric: str = "effective"
) -> dict[str, Any]:
    org = db.get(Organization, org_id)
    aliases = tag_aliases(org)
    currency = org.default_currency if org else "USD"
    target = _resolve(dimension, aliases)
    rules = sorted(
        db.scalars(select(AllocationRule).where(AllocationRule.dimension == dimension)),
        key=lambda r: (r.position, r.name),
    )
    rule_filters = [{_resolve(d, aliases): v for d, v in (r.filters or {}).items()} for r in rules]
    dims = sorted({target, *[d for f in rule_filters for d in f]})
    table = Base.metadata.tables["cost_line_items" if _needs_lines(dims) else "cost_daily"]
    end = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
    value = table.c.effective_cost if metric == "effective" else table.c.billed_cost
    amount = converted(
        value, table.c.billing_currency, table.c.charge_date, currency, table.c.org_id
    )
    cols = [_expr(table, d).label(f"d{i}") for i, d in enumerate(dims)]
    rows = db.execute(
        select(*cols, func.sum(amount))
        .where(table.c.org_id == org_id, table.c.charge_date >= month, table.c.charge_date < end)
        .group_by(*cols)
    ).all()
    direct: dict[str, Decimal] = defaultdict(Decimal)
    pools: dict[int, Decimal] = defaultdict(Decimal)
    total = Decimal(0)
    for row in rows:
        values = {d: _label(d, row[i]) for i, d in enumerate(dims)}
        cost = Decimal(str(row[-1] or 0))
        total += cost
        rule = next((i for i, f in enumerate(rule_filters) if _matches(f, values)), None)
        if rule is None:
            direct[values[target]] += cost
        else:
            pools[rule] += cost
    direct_cents = (
        split_cents(sum(direct.values(), Decimal(0)), {k: float(v) for k, v in direct.items()})
        if direct
        else {}
    )
    shared: dict[str, dict[str, Decimal]] = defaultdict(dict)
    named = {k for k in direct if k != UNTAGGED}
    for i, rule in enumerate(rules):
        pool = pools.get(i, Decimal(0))
        if not pool:
            continue
        if rule.method == "fixed":
            weights = {k: float(v) for k, v in (rule.percents or {}).items()}
        else:
            keys = [str(t) for t in rule.targets] if rule.targets else sorted(named)
            if not keys:
                keys = [UNTAGGED]
            weights = {
                k: (float(direct.get(k, 0)) if rule.method == "proportional" else 1.0) for k in keys
            }
            if rule.method == "proportional" and not any(weights.values()):
                weights = dict.fromkeys(keys, 1.0)  # nobody has direct cost: split evenly
        for k, v in split_cents(pool, weights).items():
            shared[k][rule.name] = v
    keys = sorted(set(direct_cents) | set(shared), key=lambda k: (k == UNTAGGED, k))
    out_rows = []
    for k in keys:
        d = direct_cents.get(k, Decimal(0))
        s = sum(shared.get(k, {}).values(), Decimal(0))
        out_rows.append(
            {
                "value": k,
                "direct": float(d),
                "shared": {n: float(v) for n, v in shared.get(k, {}).items()},
                "shared_total": float(s),
                "total": float(d + s),
            }
        )
    out_rows.sort(key=lambda r: (r["value"] == UNTAGGED, -r["total"]))
    total_cents = sum((Decimal(str(r["total"])) for r in out_rows), Decimal(0))
    bill_cents = sum(direct_cents.values(), Decimal(0)) + sum(
        split_cents(p, {"x": 1}).get("x", Decimal(0)) for p in pools.values()
    )
    return {
        "month": month.strftime("%Y-%m"),
        "dimension": dimension,
        "currency": currency,
        "total": float(total.quantize(CENT, ROUND_HALF_UP)),
        "allocated_total": float(total_cents),
        "adds_up": total_cents == bill_cents,
        "rules": [
            {
                "name": r.name,
                "pool": float(pools.get(i, Decimal(0)).quantize(CENT, ROUND_HALF_UP)),
                "method": r.method,
            }
            for i, r in enumerate(rules)
        ],
        "rows": out_rows,
    }


def check_rule(method: str, targets: list[str], percents: dict[str, float]) -> None:
    if method == "fixed":
        if not percents:
            raise AppError("Give each value's percentage.", code="percents_required")
        if abs(sum(percents.values()) - 100) > 0.001:
            raise AppError("The percentages must add up to 100.", code="percents_not_100")
