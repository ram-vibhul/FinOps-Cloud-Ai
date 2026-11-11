"""Spend analytics on the daily rollup: summaries, series, movers, forecast.

Amounts are in one currency at a time (the organisation's display currency by
default); spend in other currencies is reported separately until exchange rates
arrive (2.11). ``metric``: ``effective`` (amortised, after discounts - default) or
``billed`` (what the invoice shows).
"""

from __future__ import annotations

import math
import statistics
import uuid
from calendar import monthrange
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ccradar.costs.fx import converted
from ccradar.models import CostDaily

Metric = Literal["effective", "billed"]
Dimension = Literal[
    "provider", "service", "account", "region", "charge_category", "pricing_category", "none"
]
DIMENSIONS: dict[str, Any] = {
    "provider": CostDaily.provider,
    "service": CostDaily.service_name,
    "account": CostDaily.sub_account_id,
    "region": CostDaily.region,
    "charge_category": CostDaily.charge_category,
    "pricing_category": CostDaily.pricing_category,
}
RECURRING = ("Purchase", "Tax")  # lumpy monthly charges; forecast separately
OTHER = "Other"


_DISPLAY: dict[str, str] = {}


def _amount(metric: Metric, currency: str | None = None) -> Any:
    """Amount in the display currency (rows in other currencies converted per day)."""
    raw = CostDaily.effective_cost if metric == "effective" else CostDaily.billed_cost
    if currency is None:
        return raw
    return converted(
        raw, CostDaily.billing_currency, CostDaily.charge_date, currency, CostDaily.org_id
    )


def _f(value: Any) -> float:
    return float(value or 0)


def _base(org_id: uuid.UUID, currency: str, start: date, end: date) -> Any:
    del currency  # every currency is included and converted (see _amount)
    return (
        CostDaily.org_id == org_id,
        CostDaily.charge_date >= start,
        CostDaily.charge_date <= end,
    )


def latest_date(db: Session, org_id: uuid.UUID) -> date | None:
    return db.scalar(select(func.max(CostDaily.charge_date)).where(CostDaily.org_id == org_id))


def currencies(
    db: Session, org_id: uuid.UUID, start: date, end: date, metric: Metric, display: str
) -> dict[str, float]:
    """Spend per original currency that could not be converted to ``display`` (no rate)."""
    raw = CostDaily.effective_cost if metric == "effective" else CostDaily.billed_cost
    rows = db.execute(
        select(CostDaily.billing_currency, func.sum(raw))
        .where(
            CostDaily.org_id == org_id,
            CostDaily.charge_date >= start,
            CostDaily.charge_date <= end,
            CostDaily.billing_currency != display,
            _amount(metric, display).is_(None),
        )
        .group_by(CostDaily.billing_currency)
    ).all()
    return {c: round(_f(v), 2) for c, v in rows if v}


def daily_totals(
    db: Session,
    org_id: uuid.UUID,
    currency: str,
    start: date,
    end: date,
    metric: Metric,
    categories: tuple[str, ...] | None = None,
    exclude: tuple[str, ...] = (),
) -> dict[date, float]:
    stmt = (
        select(CostDaily.charge_date, func.sum(_amount(metric, currency)))
        .where(*_base(org_id, currency, start, end))
        .group_by(CostDaily.charge_date)
    )
    if categories:
        stmt = stmt.where(CostDaily.charge_category.in_(categories))
    if exclude:
        stmt = stmt.where(CostDaily.charge_category.not_in(exclude))
    return {d: _f(v) for d, v in db.execute(stmt).all()}


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


@dataclass
class Forecast:
    month_to_date: float
    forecast: float
    low: float
    high: float
    daily_rate: float
    recurring_expected: float
    data_through: date | None
    method: str


def forecast_month(
    db: Session, org_id: uuid.UUID, currency: str, metric: Metric, today: date
) -> Forecast:
    """Month-end forecast: actual so far + weekday-weighted recent daily rate for the
    remaining days + recurring charges (purchases, tax) seen last month but not yet
    this month. The range widens with the remaining days and the day-to-day noise."""
    first = today.replace(day=1)
    last_day = first.replace(day=monthrange(first.year, first.month)[1])
    through = latest_date(db, org_id)
    if through is None or through < first - timedelta(days=60):
        return Forecast(0, 0, 0, 0, 0, 0, through, "no data")
    through = min(through, today)
    mtd = sum(daily_totals(db, org_id, currency, first, through, metric).values())

    window_start = through - timedelta(days=27)
    usage = daily_totals(db, org_id, currency, window_start, through, metric, exclude=RECURRING)
    series = [usage.get(d, 0.0) for d in _days(window_start, through)]
    recent = series[-14:]
    rate = statistics.fmean(recent) if recent else 0.0
    weekday_avg: dict[int, list[float]] = defaultdict(list)
    for d, v in zip(_days(window_start, through), series, strict=True):
        weekday_avg[d.weekday()].append(v)
    overall = statistics.fmean(series) if series and any(series) else 0.0
    factors = (
        {wd: (statistics.fmean(vs) / overall if overall else 1.0) for wd, vs in weekday_avg.items()}
        if len(series) >= 14
        else {}
    )
    remaining = _days(through + timedelta(days=1), last_day) if through < last_day else []
    projected = sum(rate * factors.get(d.weekday(), 1.0) for d in remaining)

    # Recurring charges from last month that have not happened yet this month.
    prev_first = (first - timedelta(days=1)).replace(day=1)
    prev_last = first - timedelta(days=1)
    prev_recurring = daily_totals(
        db, org_id, currency, prev_first, prev_last, metric, categories=RECURRING
    )
    this_recurring = daily_totals(
        db, org_id, currency, first, through, metric, categories=RECURRING
    )
    seen_days = {d.day for d in this_recurring}
    recurring = sum(
        v for d, v in prev_recurring.items() if d.day > through.day and d.day not in seen_days
    )

    residuals = [
        v - rate * factors.get(d.weekday(), 1.0)
        for d, v in zip(_days(window_start, through), series, strict=True)
    ][-14:]
    noise = statistics.pstdev(residuals) if len(residuals) > 1 else rate * 0.1
    spread = 1.645 * noise * math.sqrt(max(len(remaining), 1)) if remaining else 0.0
    total = mtd + projected + recurring
    return Forecast(
        month_to_date=round(mtd, 2),
        forecast=round(total, 2),
        low=round(max(mtd, total - spread), 2),
        high=round(total + spread, 2),
        daily_rate=round(rate, 2),
        recurring_expected=round(recurring, 2),
        data_through=through,
        method="weekday-weighted 14-day rate + recurring charges",
    )


def summary(
    db: Session, org_id: uuid.UUID, currency: str, metric: Metric, today: date
) -> dict[str, Any]:
    fc = forecast_month(db, org_id, currency, metric, today)
    through = fc.data_through or today
    first = today.replace(day=1)
    prev_first = (first - timedelta(days=1)).replace(day=1)
    prev_last = first - timedelta(days=1)
    same_day_prev = prev_first.replace(day=min(through.day, prev_last.day))
    last_month = sum(daily_totals(db, org_id, currency, prev_first, prev_last, metric).values())
    prev_mtd = sum(daily_totals(db, org_id, currency, prev_first, same_day_prev, metric).values())
    yesterday = daily_totals(db, org_id, currency, through, through, metric).get(through, 0.0)
    return {
        "currency": currency,
        "metric": metric,
        "data_through": fc.data_through.isoformat() if fc.data_through else None,
        "month_to_date": fc.month_to_date,
        "previous_month_to_date": round(prev_mtd, 2),
        "change_vs_previous_month_pct": _pct(fc.month_to_date, prev_mtd),
        "last_month": round(last_month, 2),
        "latest_day": round(yesterday, 2),
        "forecast": {
            "amount": fc.forecast,
            "low": fc.low,
            "high": fc.high,
            "daily_rate": fc.daily_rate,
            "recurring_expected": fc.recurring_expected,
            "change_vs_last_month_pct": _pct(fc.forecast, last_month),
            "method": fc.method,
        },
        # Spend we could not convert yet (no exchange rate for that currency/day).
        "other_currencies": currencies(db, org_id, prev_first, through, metric, currency),
    }


PROVIDER_NAMES = {
    "aws": "AWS",
    "azure": "Microsoft Azure",
    "gcp": "Google Cloud",
    "kubernetes": "Kubernetes",
    "snowflake": "Snowflake",
    "databricks": "Databricks",
    "datadog": "Datadog",
    "mongodb_atlas": "MongoDB Atlas",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "other": "Other (uploaded files)",
}


def provider_name(provider: str) -> str:
    return PROVIDER_NAMES.get(provider, provider.replace("_", " ").title())


def by_provider(
    db: Session, org_id: uuid.UUID, currency: str, metric: Metric, today: date
) -> dict[str, Any]:
    """This month, last month (same days and full) and connection health per provider,
    all in ``currency`` (2.16)."""
    from ccradar.models import CloudConnection

    through = latest_date(db, org_id) or today
    first = through.replace(day=1)
    prev_last = first - timedelta(days=1)
    prev_first = prev_last.replace(day=1)
    same_day_prev = prev_first.replace(day=min(through.day, prev_last.day))

    def totals(start: date, end: date) -> dict[str, float]:
        rows = db.execute(
            select(CostDaily.provider, func.sum(_amount(metric, currency)))
            .where(*_base(org_id, currency, start, end))
            .group_by(CostDaily.provider)
        ).all()
        return {p: _f(v) for p, v in rows}

    mtd, prev_mtd = totals(first, through), totals(prev_first, same_day_prev)
    last_month = totals(prev_first, prev_last)
    latest = dict(
        db.execute(
            select(CostDaily.provider, func.max(CostDaily.charge_date))
            .where(CostDaily.org_id == org_id)
            .group_by(CostDaily.provider)
        ).all()
    )
    sources: dict[str, list[dict[str, Any]]] = {}
    for c in db.scalars(select(CloudConnection).order_by(CloudConnection.name)):
        sources.setdefault(c.provider, []).append(
            {"id": str(c.id), "name": c.name, "status": c.status, "error": c.last_error}
        )
    total = sum(mtd.values())
    rows = []
    for p in sorted(set(mtd) | set(last_month) | set(sources), key=lambda k: -mtd.get(k, 0.0)):
        rows.append(
            {
                "provider": p,
                "name": provider_name(p),
                "month_to_date": round(mtd.get(p, 0.0), 2),
                "previous_month_to_date": round(prev_mtd.get(p, 0.0), 2),
                "change_pct": _pct(mtd.get(p, 0.0), prev_mtd.get(p, 0.0)),
                "last_month": round(last_month.get(p, 0.0), 2),
                "share_pct": round(mtd.get(p, 0.0) / total * 100, 1) if total else 0.0,
                "data_through": latest[p].isoformat() if latest.get(p) else None,
                "connections": sources.get(p, []),
            }
        )
    return {
        "currency": currency,
        "metric": metric,
        "data_through": through.isoformat(),
        "month_to_date": round(total, 2),
        "providers": rows,
        "other_currencies": currencies(db, org_id, first, through, metric, currency),
    }


def _pct(current: float, previous: float) -> float | None:
    if not previous:
        return None
    return round((current - previous) / abs(previous) * 100, 1)


def series(
    db: Session,
    org_id: uuid.UUID,
    currency: str,
    metric: Metric,
    start: date,
    end: date,
    dimension: Dimension,
    top: int = 7,
    granularity: Literal["day", "month"] = "day",
) -> dict[str, Any]:
    """Stacked series: the ``top`` largest groups, the rest folded into "Other"."""
    if granularity == "month":
        # Group by the first day of the month portably: year*100+month.
        period = func.extract("year", CostDaily.charge_date) * 100 + func.extract(
            "month", CostDaily.charge_date
        )
    else:
        period = CostDaily.charge_date
    key_col = DIMENSIONS.get(dimension)
    keys: list[Any] = [period] + ([key_col] if key_col is not None else [])
    stmt = (
        select(*keys, func.sum(_amount(metric, currency)))
        .where(*_base(org_id, currency, start, end))
        .group_by(*keys)
    )
    rows = db.execute(stmt).all()

    def label_of(p: Any) -> str:
        if granularity == "day":
            return p.isoformat()
        p = int(p)
        return f"{p // 100:04d}-{p % 100:02d}"

    if granularity == "day":
        periods = [d.isoformat() for d in _days(start, end)]
    else:
        periods, cur = [], start.replace(day=1)
        while cur <= end:
            periods.append(cur.isoformat()[:7])
            cur = (cur + timedelta(days=32)).replace(day=1)
    totals_by_key: dict[str, float] = defaultdict(float)
    cells: dict[tuple[str, str], float] = defaultdict(float)
    for row in rows:
        p = label_of(row[0])
        k = (str(row[1]) if row[1] is not None else "(none)") if key_col is not None else "Total"
        v = _f(row[-1])
        totals_by_key[k] += v
        cells[(p, k)] += v
    ranked = sorted(totals_by_key, key=lambda k: -abs(totals_by_key[k]))
    keep, rest = ranked[:top], ranked[top:]
    out = []
    for k in keep:
        out.append(
            {
                "key": k,
                "label": k,
                "total": round(totals_by_key[k], 2),
                "values": [round(cells.get((p, k), 0.0), 2) for p in periods],
            }
        )
    if rest:
        out.append(
            {
                "key": OTHER,
                "label": f"{OTHER} ({len(rest)})",
                "total": round(sum(totals_by_key[k] for k in rest), 2),
                "values": [round(sum(cells.get((p, k), 0.0) for k in rest), 2) for p in periods],
            }
        )
    return {
        "currency": currency,
        "metric": metric,
        "dimension": dimension,
        "granularity": granularity,
        "periods": periods,
        "series": out,
        "total": round(sum(totals_by_key.values()), 2),
    }


def movers(
    db: Session,
    org_id: uuid.UUID,
    currency: str,
    metric: Metric,
    dimension: Dimension,
    end: date,
    days: int,
    limit: int = 10,
    include_purchases: bool = False,
) -> dict[str, Any]:
    """Largest absolute changes: last ``days`` days vs the ``days`` before.

    Purchases and tax are excluded by default: a monthly Savings Plan payment would
    otherwise top the list whenever it falls in one window and not the other.
    """
    key_col = DIMENSIONS.get(dimension) or CostDaily.service_name
    cur_start = end - timedelta(days=days - 1)
    prev_end = cur_start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=days - 1)

    def totals(a: date, b: date) -> dict[str, float]:
        stmt = (
            select(key_col, func.sum(_amount(metric, currency)))
            .where(*_base(org_id, currency, a, b))
            .group_by(key_col)
        )
        if not include_purchases:
            stmt = stmt.where(CostDaily.charge_category.not_in(RECURRING))
        rows = db.execute(stmt).all()
        return {(str(k) if k is not None else "(none)"): _f(v) for k, v in rows}

    now, before = totals(cur_start, end), totals(prev_start, prev_end)
    items = []
    for key in set(now) | set(before):
        c, p = now.get(key, 0.0), before.get(key, 0.0)
        if abs(c - p) < 0.005:
            continue
        items.append(
            {
                "key": key,
                "current": round(c, 2),
                "previous": round(p, 2),
                "change": round(c - p, 2),
                "change_pct": _pct(c, p),
                "status": "new" if p == 0 else "gone" if c == 0 else "changed",
            }
        )
    items.sort(key=lambda i: -abs(i["change"]))
    return {
        "currency": currency,
        "metric": metric,
        "dimension": dimension,
        "current_period": [cur_start.isoformat(), end.isoformat()],
        "previous_period": [prev_start.isoformat(), prev_end.isoformat()],
        "items": items[:limit],
    }


__all__ = ["Decimal", "forecast_month", "movers", "series", "summary"]
