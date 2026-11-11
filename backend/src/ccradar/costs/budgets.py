"""Budget tracking: spend so far in the period, the forecast for its end, children
rolled up, and alerts when spend or forecast crosses a threshold (once per period)."""

from __future__ import annotations

import statistics
import uuid
from calendar import monthrange
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ccradar.core.email import Email
from ccradar.core.notify import Message, notify
from ccradar.costs.explorer import _filter_clauses, _needs_lines, _resolve, tag_aliases
from ccradar.costs.fx import converted
from ccradar.db.base import Base, utcnow
from ccradar.models import Budget, Organization


def period_bounds(period: str, today: date) -> tuple[date, date, str]:
    if period == "quarterly":
        q = (today.month - 1) // 3
        start = date(today.year, 3 * q + 1, 1)
        end_month = 3 * q + 3
        end = date(today.year, end_month, monthrange(today.year, end_month)[1])
        return start, end, f"{today.year}-Q{q + 1}"
    if period == "annual":
        return date(today.year, 1, 1), date(today.year, 12, 31), str(today.year)
    start = today.replace(day=1)
    return start, start.replace(day=monthrange(start.year, start.month)[1]), start.strftime("%Y-%m")


def effective_filters(chain: list[Budget], aliases: dict[str, str]) -> dict[str, list[str]] | None:
    """Root-to-leaf filters ANDed together; None when a child excludes everything."""
    out: dict[str, list[str]] = {}
    for b in chain:
        for dim, values in (b.filters or {}).items():
            key = _resolve(dim, aliases)
            if key in out:
                both = [v for v in out[key] if v in values]
                if not both:
                    return None
                out[key] = both
            else:
                out[key] = list(values)
    return out


def daily_spend(
    db: Session,
    org_id: uuid.UUID,
    filters: dict[str, list[str]],
    start: date,
    end: date,
    metric: str,
    currency: str,
) -> dict[date, float]:
    table = Base.metadata.tables["cost_line_items" if _needs_lines(list(filters)) else "cost_daily"]
    value = table.c.effective_cost if metric == "effective" else table.c.billed_cost
    amount = converted(
        value, table.c.billing_currency, table.c.charge_date, currency, table.c.org_id
    )
    rows = db.execute(
        select(table.c.charge_date, func.sum(amount))
        .where(
            table.c.org_id == org_id,
            table.c.charge_date >= start,
            table.c.charge_date <= end,
            *_filter_clauses(table, filters),
        )
        .group_by(table.c.charge_date)
    ).all()
    return {d: float(v or 0) for d, v in rows}


def _chain(b: Budget, by_id: dict[uuid.UUID, Budget]) -> list[Budget]:
    chain, seen = [b], {b.id}
    while chain[0].parent_id and chain[0].parent_id in by_id and chain[0].parent_id not in seen:
        parent = by_id[chain[0].parent_id]
        chain.insert(0, parent)
        seen.add(parent.id)
    return chain


def status(
    db: Session, org_id: uuid.UUID, b: Budget, by_id: dict[uuid.UUID, Budget], today: date
) -> dict[str, Any]:
    org = db.get(Organization, org_id)
    start, end, key = period_bounds(b.period, today)
    filters = effective_filters(_chain(b, by_id), tag_aliases(org))
    through = min(today, end)
    daily = (
        daily_spend(db, org_id, filters, start, through, b.metric, b.currency)
        if filters is not None
        else {}
    )
    spent = sum(daily.values())
    last_data = max(daily) if daily else None
    recent = [daily.get(last_data - timedelta(days=i), 0.0) for i in range(14)] if last_data else []
    rate = statistics.fmean(recent) if recent else 0.0
    remaining = (end - (last_data or through)).days if last_data else (end - through).days + 1
    forecast = spent + rate * max(remaining, 0)
    children = [c for c in by_id.values() if c.parent_id == b.id]
    allocated = sum(c.amount for c in children if c.currency == b.currency)
    return {
        "id": str(b.id),
        "parent_id": str(b.parent_id) if b.parent_id else None,
        "name": b.name,
        "amount": b.amount,
        "currency": b.currency,
        "period": b.period,
        "period_key": key,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "metric": b.metric,
        "filters": b.filters or {},
        "effective_filters": filters,
        "thresholds": b.thresholds or [],
        "alert_on_forecast": b.alert_on_forecast,
        "recipients": b.recipients or [],
        "owner_id": str(b.owner_id) if b.owner_id else None,
        "spent": round(spent, 2),
        "forecast": round(forecast, 2),
        "spent_pct": round(100 * spent / b.amount, 1) if b.amount else None,
        "forecast_pct": round(100 * forecast / b.amount, 1) if b.amount else None,
        "children": [str(c.id) for c in children],
        "allocated_to_children": round(allocated, 2),
        "over_allocated": allocated > b.amount,
        "daily": [{"date": d.isoformat(), "cost": round(v, 2)} for d, v in sorted(daily.items())],
    }


def all_status(db: Session, org_id: uuid.UUID, today: date | None = None) -> list[dict[str, Any]]:
    today = today or utcnow().date()
    budgets = list(db.scalars(select(Budget)))
    by_id = {b.id: b for b in budgets}
    return [status(db, org_id, b, by_id, today) for b in budgets]


def check_alerts(
    db: Session, org_id: uuid.UUID, today: date | None = None, enqueue: Any = None
) -> int:
    """Send each crossed threshold once per period (actual first, then forecast)."""
    today = today or utcnow().date()
    budgets = list(db.scalars(select(Budget)))
    by_id = {b.id: b for b in budgets}
    messages, sent = [], 0
    for b in budgets:
        s = status(db, org_id, b, by_id, today)
        state = dict(b.alert_state or {})
        done = set(state.get(s["period_key"], []))
        new = []
        for t in sorted(b.thresholds or []):
            if s["spent_pct"] is not None and s["spent_pct"] >= t and f"actual:{t}" not in done:
                new.append(
                    (
                        f"actual:{t}",
                        f"{b.name}: {s['spent_pct']:.0f}% of the budget spent",
                        f"{s['spent']:,.2f} of {b.amount:,.2f} {b.currency} spent so far this "
                        f"period (forecast {s['forecast']:,.2f}).",
                    )
                )
            elif (
                b.alert_on_forecast
                and s["forecast_pct"] is not None
                and s["forecast_pct"] >= t
                and f"forecast:{t}" not in done
                and f"actual:{t}" not in done
            ):
                new.append(
                    (
                        f"forecast:{t}",
                        f"{b.name}: on course for {s['forecast_pct']:.0f}% of the budget",
                        f"Forecast {s['forecast']:,.2f} of {b.amount:,.2f} {b.currency} by "
                        f"{s['period_end']}; {s['spent']:,.2f} spent so far.",
                    )
                )
        if not new:
            continue
        state = {s["period_key"]: sorted(done | {k for k, _, _ in new})}  # older periods dropped
        b.alert_state = state
        for _, title, text in new:
            messages.append(Message(title=title, text=text, link="/governance?tab=budgets"))
            for to in b.recipients or []:
                if enqueue is not None:
                    enqueue(
                        "email.send",
                        message=Email(
                            to=to, subject=f"CloudCostRadar budget alert: {title}", text=text
                        ).to_dict(),
                    )
            sent += 1
    db.flush()
    if messages:
        notify(db, "budget_alert", messages)
    return sent
