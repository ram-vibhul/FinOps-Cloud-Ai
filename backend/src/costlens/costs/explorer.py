"""Cost explorer: group and filter spend by any dimension, including tags.

Dimensions: ``service``, ``account``, ``region``, ``charge_category``,
``pricing_category``, ``usage_type``, ``resource``, ``team``, ``project`` (aliases for
the organisation's tag keys, default ``team`` / ``project``) and ``tag:<key>``.

Queries use the daily rollup when every dimension and filter is in it, and the
detailed line items otherwise (tags, resources, usage types). Tag lookups use
SQLAlchemy's JSON operators, so the key is always a bound parameter.
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Table, func, literal, select
from sqlalchemy.orm import Session

from costlens.core.errors import AppError
from costlens.costs.fx import converted
from costlens.db.base import Base
from costlens.models import Organization

ROLLUP_DIMENSIONS = {
    "service": "service_name",
    "account": "sub_account_id",
    "region": "region",
    "charge_category": "charge_category",
    "pricing_category": "pricing_category",
}
LINE_DIMENSIONS = {**ROLLUP_DIMENSIONS, "usage_type": "usage_type", "resource": "resource_id"}
TAG_KEY = re.compile(r"^[\w.:/@+=\- ]{1,128}$")
UNTAGGED = "(untagged)"
NONE = "(none)"
MAX_GROUPS = 500


class ExploreQuery(BaseModel):
    start: date
    end: date
    metric: Literal["effective", "billed"] = "effective"
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    group_by: list[str] = Field(default_factory=lambda: ["service"], max_length=2)
    filters: dict[str, list[str]] = Field(default_factory=dict)
    granularity: Literal["total", "day", "month"] = "day"
    top: int = Field(default=7, ge=1, le=20)
    limit: int = Field(default=100, ge=1, le=MAX_GROUPS)

    @field_validator("group_by", "filters")
    @classmethod
    def _dimensions(cls, value: Any) -> Any:
        names = value if isinstance(value, list) else list(value)
        for name in names:
            check_dimension(name)
        return value


def check_dimension(name: str) -> None:
    if name in LINE_DIMENSIONS or name in ("team", "project"):
        return
    if name.startswith("tag:") and TAG_KEY.match(name[4:]):
        return
    if name.startswith("vtag:") and TAG_KEY.match(name[5:]):
        return
    raise ValueError(f"unknown dimension {name!r}")


def tag_aliases(org: Organization | None) -> dict[str, str]:
    dims = ((org.settings if org else None) or {}).get("dimensions", {})
    return {"team": dims.get("team_tag", "team"), "project": dims.get("project_tag", "project")}


def _resolve(name: str, aliases: dict[str, str]) -> str:
    if name not in aliases:
        return name
    target = aliases[name]
    return target if target.startswith("vtag:") else f"tag:{target}"


def _needs_lines(dims: list[str]) -> bool:
    return any(d not in ROLLUP_DIMENSIONS for d in dims)


def _expr(table: Table, dim: str) -> Any:
    if dim.startswith("tag:"):
        return table.c.tags[dim[4:]].as_string()
    if dim.startswith("vtag:"):
        return table.c.vtags[dim[5:]].as_string()
    return table.c[LINE_DIMENSIONS[dim]]


def _label(dim: str, value: Any) -> str:
    if value is None or value == "":
        return UNTAGGED if dim.startswith(("tag:", "vtag:")) else NONE
    return str(value)


def explore(db: Session, org_id: uuid.UUID, q: ExploreQuery) -> dict[str, Any]:
    if q.start > q.end or (q.end - q.start).days > 400:
        raise AppError("Choose a range of up to 400 days.", code="invalid_range")
    org = db.get(Organization, org_id)
    aliases = tag_aliases(org)
    currency = q.currency or (org.default_currency if org else "USD")
    group_by = [_resolve(d, aliases) for d in q.group_by]
    filters = {_resolve(d, aliases): v for d, v in q.filters.items()}
    use_lines = _needs_lines(group_by + list(filters))
    table = Base.metadata.tables["cost_line_items" if use_lines else "cost_daily"]
    raw = table.c.effective_cost if q.metric == "effective" else table.c.billed_cost
    amount = converted(raw, table.c.billing_currency, table.c.charge_date, currency, table.c.org_id)
    where = [
        table.c.org_id == org_id,
        table.c.charge_date >= q.start,
        table.c.charge_date <= q.end,
    ]
    where += _filter_clauses(table, filters)

    keys = [_expr(table, d).label(f"g{i}") for i, d in enumerate(group_by)]
    period: Any = literal("all")
    if q.granularity == "day":
        period = table.c.charge_date
    elif q.granularity == "month":
        period = func.extract("year", table.c.charge_date) * 100 + func.extract(
            "month", table.c.charge_date
        )
    rows = db.execute(
        select(period.label("p"), *keys, func.sum(amount)).where(*where).group_by(period, *keys)
    ).all()

    totals: dict[tuple[str, ...], float] = defaultdict(float)
    cells: dict[tuple[str, tuple[str, ...]], float] = defaultdict(float)
    for row in rows:
        p = row[0]
        if q.granularity == "day":
            p = p.isoformat()
        elif q.granularity == "month":
            p = f"{int(p) // 100:04d}-{int(p) % 100:02d}"
        if row[-1] is None:  # nothing convertible to the display currency (no rate)
            continue
        group = tuple(_label(d, row[1 + i]) for i, d in enumerate(group_by))
        value = float(row[-1])
        totals[group] += value
        cells[(str(p), group)] += value

    previous = _previous_totals(db, table, amount, where, keys, group_by, q)
    grand = sum(totals.values())
    ranked = sorted(totals, key=lambda g: -abs(totals[g]))
    groups = [
        {
            "key": list(g),
            "label": " · ".join(g),
            "total": round(totals[g], 2),
            "share": round(totals[g] / grand * 100, 1) if grand else 0.0,
            "previous": round(previous.get(g, 0.0), 2),
            "change_pct": _pct(totals[g], previous.get(g, 0.0)),
        }
        for g in ranked[: q.limit]
    ]

    periods = _periods(q)
    series = []
    if q.granularity != "total":
        top, rest = ranked[: q.top], ranked[q.top :]
        for g in top:
            series.append(
                {
                    "key": " · ".join(g),
                    "label": " · ".join(g),
                    "total": round(totals[g], 2),
                    "values": [round(cells.get((p, g), 0.0), 2) for p in periods],
                }
            )
        if rest:
            series.append(
                {
                    "key": "Other",
                    "label": f"Other ({len(rest)})",
                    "total": round(sum(totals[g] for g in rest), 2),
                    "values": [
                        round(sum(cells.get((p, g), 0.0) for g in rest), 2) for p in periods
                    ],
                }
            )
    _label_accounts(db, group_by, groups, series)
    if use_lines and "resource" in group_by:
        _attach_resource_details(db, table, where, groups, group_by.index("resource"))
    return {
        "currency": currency,
        "metric": q.metric,
        "group_by": q.group_by,
        "source": "line_items" if use_lines else "daily",
        "total": round(grand, 2),
        "group_count": len(totals),
        "groups": groups,
        "periods": periods,
        "series": series,
    }


def _filter_clauses(table: Table, filters: dict[str, list[str]]) -> list[Any]:
    """Each dimension: value IN (...), with "(untagged)"/"(none)" meaning NULL."""
    out = []
    for dim, values in filters.items():
        expr = _expr(table, dim)
        real = [v for v in values if v not in (UNTAGGED, NONE)]
        clauses = [expr.in_(real)] if real else []
        if len(real) != len(values):
            clauses.append(expr.is_(None))
        if clauses:
            out.append(clauses[0] if len(clauses) == 1 else clauses[0] | clauses[1])
    return out


def _previous_totals(
    db: Session,
    table: Table,
    amount: Any,
    where: list[Any],
    keys: list[Any],
    group_by: list[str],
    q: ExploreQuery,
) -> dict[tuple[str, ...], float]:
    length = (q.end - q.start).days + 1
    prev_end, prev_start = q.start - timedelta(days=1), q.start - timedelta(days=length)
    base = [w for w in where if "charge_date" not in str(w)]
    rows = db.execute(
        select(*keys, func.sum(amount))
        .where(*base, table.c.charge_date >= prev_start, table.c.charge_date <= prev_end)
        .group_by(*keys)
    ).all()
    return {tuple(_label(d, r[i]) for i, d in enumerate(group_by)): float(r[-1] or 0) for r in rows}


def _label_accounts(
    db: Session, group_by: list[str], groups: list[dict[str, Any]], series: list[dict[str, Any]]
) -> None:
    """Show "Name (id)" for accounts; keys stay raw ids so they work as filters."""
    if "account" not in group_by:
        return
    from costlens.models import CloudAccount

    index = group_by.index("account")
    names = dict(db.execute(select(CloudAccount.external_id, CloudAccount.name)).all())

    def pretty(parts: list[str]) -> str:
        shown = list(parts)
        name = names.get(parts[index])
        if name:
            shown[index] = f"{name} ({parts[index]})"
        return " · ".join(shown)

    by_label = {}
    for g in groups:
        new = pretty(g["key"])
        by_label[g["label"]] = new
        g["label"] = new
    for s in series:
        if s["key"] in by_label:
            s["label"] = by_label[s["key"]]


def _attach_resource_details(
    db: Session, table: Table, where: list[Any], groups: list[dict[str, Any]], index: int
) -> None:
    ids = [g["key"][index] for g in groups if g["key"][index] != NONE]
    if not ids:
        return
    rows = db.execute(
        select(
            table.c.resource_id,
            func.max(table.c.resource_name),
            func.max(table.c.resource_type),
            func.max(table.c.service_name),
            func.max(table.c.region),
            func.max(table.c.sub_account_id),
        )
        .where(*where, table.c.resource_id.in_(ids))
        .group_by(table.c.resource_id)
    ).all()
    info = {
        r[0]: {"name": r[1], "type": r[2], "service": r[3], "region": r[4], "account": r[5]}
        for r in rows
    }
    for g in groups:
        g["resource"] = info.get(g["key"][index])


def _periods(q: ExploreQuery) -> list[str]:
    if q.granularity == "day":
        return [
            (q.start + timedelta(days=i)).isoformat() for i in range((q.end - q.start).days + 1)
        ]
    if q.granularity == "month":
        out, cur = [], q.start.replace(day=1)
        while cur <= q.end:
            out.append(cur.isoformat()[:7])
            cur = (cur + timedelta(days=32)).replace(day=1)
        return out
    return []


def _pct(current: float, previous: float) -> float | None:
    return round((current - previous) / abs(previous) * 100, 1) if previous else None


# --- values for filters --------------------------------------------------------------


def dimension_values(
    db: Session,
    org_id: uuid.UUID,
    dim: str,
    *,
    start: date,
    end: date,
    search: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    check_dimension(dim)
    org = db.get(Organization, org_id)
    dim = _resolve(dim, tag_aliases(org))
    table = Base.metadata.tables["cost_line_items" if _needs_lines([dim]) else "cost_daily"]
    expr = _expr(table, dim)
    stmt = (
        select(expr, func.sum(table.c.effective_cost))
        .where(table.c.org_id == org_id, table.c.charge_date >= start, table.c.charge_date <= end)
        .group_by(expr)
        .order_by(func.sum(table.c.effective_cost).desc())
        .limit(limit)
    )
    if search:
        stmt = stmt.where(expr.ilike(f"%{search}%"))
    return [
        {"value": _label(dim, v), "total": round(float(t or 0), 2)}
        for v, t in db.execute(stmt).all()
    ]


def tag_keys(
    db: Session, org_id: uuid.UUID, *, start: date, end: date, limit: int = 200
) -> list[dict[str, Any]]:
    """Tag keys seen on line items in the range, with tagged spend and coverage."""
    table = Base.metadata.tables["cost_line_items"]
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        key_expr = func.json_object_keys(table.c.tags).table_valued("value").render_derived()
    else:
        key_expr = func.json_each(table.c.tags).table_valued("key")  # SQLite: no alias list
    key_col = key_expr.c.value if dialect == "postgresql" else key_expr.c.key
    base = (table.c.org_id == org_id, table.c.charge_date >= start, table.c.charge_date <= end)
    total = float(db.scalar(select(func.sum(table.c.effective_cost)).where(*base)) or 0)
    rows = db.execute(
        select(key_col, func.sum(table.c.effective_cost), func.count())
        .select_from(table)
        .join(key_expr, literal(True))
        .where(*base)
        .group_by(key_col)
        .order_by(func.sum(table.c.effective_cost).desc())
        .limit(limit)
    ).all()
    return [
        {
            "key": k,
            "tagged_cost": round(float(c or 0), 2),
            "lines": n,
            "coverage_pct": round(float(c or 0) / total * 100, 1) if total else 0.0,
        }
        for k, c, n in rows
    ]
