"""Early warnings: tell people before the month ends (or the day ends) that spend is
running hot, instead of after the bill arrives.

- Forecast overspend: the month-end forecast is more than ``forecast_over_pct`` above
  last month (and the difference is at least the alert minimum). Once a month.
- Same-day spend: today's spend so far, from hourly billing data, is more than
  ``same_day_over_pct`` above the average of the same hours over the last 7 days. Once a
  day, after at least 4 hours of data, with the services that drive it."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ccradar.core.notify import Message, notify
from ccradar.costs import analytics
from ccradar.costs.anomalies import alert_settings
from ccradar.costs.fx import converted
from ccradar.db.base import utcnow
from ccradar.models import CostHourly, Organization

MIN_HOURS = 4
BASELINE_DAYS = 7


def forecast_status(db: Session, org: Organization, now: datetime) -> dict[str, Any] | None:
    cfg = alert_settings(org)
    s = analytics.summary(db, org.id, org.default_currency, "effective", now.date())
    last, forecast = float(s["last_month"] or 0), float(s["forecast"]["amount"] or 0)
    if not cfg.forecast_warnings or last <= 0:
        return None
    over = forecast - last
    if forecast <= last * (1 + cfg.forecast_over_pct / 100) or over < cfg.min_impact:
        return None
    end = analytics.latest_date(db, org.id) or now.date()
    movers = analytics.movers(db, org.id, org.default_currency, "effective", "service", end, 7, 3)
    drivers = [m for m in movers.get("items", []) if m.get("change", 0) > 0]
    return {
        "kind": "forecast",
        "month": now.strftime("%Y-%m"),
        "forecast": round(forecast, 2),
        "last_month": round(last, 2),
        "over_pct": round(100 * over / last, 1),
        "drivers": [
            {"key": m.get("label") or m["key"], "change": round(m["change"], 2)} for m in drivers
        ],
    }


def same_day_status(db: Session, org: Organization, now: datetime) -> dict[str, Any] | None:
    cfg = alert_settings(org)
    if not cfg.same_day_warnings or now.hour < MIN_HOURS:
        return None
    currency = org.default_currency
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    cutoff_hour = now.hour  # whole hours only
    t = CostHourly
    amount = converted(t.effective_cost, t.billing_currency, func.date(t.hour), currency, t.org_id)
    rows = db.execute(
        select(t.hour, t.service_name, func.sum(amount))
        .where(
            t.hour >= day_start - timedelta(days=BASELINE_DAYS),
            t.hour < day_start + timedelta(hours=cutoff_hour),
        )
        .group_by(t.hour, t.service_name)
    ).all()
    today: dict[str, float] = defaultdict(float)
    base: dict[str, float] = defaultdict(float)
    days_seen: set[str] = set()
    latest_today = None
    for hour, service, cost in rows:
        h = hour if hour.tzinfo else hour.replace(tzinfo=now.tzinfo)
        if h.hour >= cutoff_hour:
            continue
        if h >= day_start:
            today[service] += float(cost or 0)
            latest_today = max(latest_today or h, h)
        else:
            base[service] += float(cost or 0)
            days_seen.add(h.date().isoformat())
    if latest_today is None or not days_seen:
        return None  # no hourly data for today (or no history)
    typical = {k: v / len(days_seen) for k, v in base.items()}
    so_far, usual = sum(today.values()), sum(typical.values())
    over = so_far - usual
    if (
        usual <= 0
        or so_far <= usual * (1 + cfg.same_day_over_pct / 100)
        or over < cfg.min_impact / 10
    ):
        return None
    drivers = sorted(
        ((k, today.get(k, 0) - typical.get(k, 0)) for k in set(today) | set(typical)),
        key=lambda kv: -kv[1],
    )[:3]
    return {
        "kind": "same_day",
        "day": now.date().isoformat(),
        "hours": cutoff_hour,
        "so_far": round(so_far, 2),
        "usual": round(usual, 2),
        "over_pct": round(100 * over / usual, 1),
        "drivers": [{"key": k, "change": round(v, 2)} for k, v in drivers if v > 0],
    }


def current(db: Session, org_id: uuid.UUID, now: datetime | None = None) -> list[dict[str, Any]]:
    org = db.get(Organization, org_id)
    if org is None:
        return []
    now = now or utcnow()
    return [w for w in (forecast_status(db, org, now), same_day_status(db, org, now)) if w]


def check(db: Session, org_id: uuid.UUID, now: datetime | None = None) -> int:
    """Notify each warning once (forecast: per month; same day: per day)."""
    org = db.get(Organization, org_id)
    if org is None:
        return 0
    now = now or utcnow()
    state = dict((org.settings or {}).get("early_warning_state") or {})
    cur = org.default_currency
    messages = []
    for w in current(db, org_id, now):
        stamp = w.get("month") or w.get("day")
        if state.get(w["kind"]) == stamp:
            continue
        state[w["kind"]] = stamp
        drivers = (
            ", ".join(f"{d['key']} (+{d['change']:,.2f})" for d in w["drivers"])
            or "no single service stands out"
        )
        if w["kind"] == "forecast":
            messages.append(
                Message(
                    title=f"Month-end forecast {w['over_pct']:.0f}% above last month",
                    text=f"Forecast {w['forecast']:,.2f} {cur} vs {w['last_month']:,.2f} last "
                    f"month. Growing most: {drivers}.",
                    link="/costs",
                )
            )
        else:
            messages.append(
                Message(
                    title=f"Spend today is {w['over_pct']:.0f}% above normal",
                    text=f"{w['so_far']:,.2f} {cur} in the first {w['hours']} hours vs a usual "
                    f"{w['usual']:,.2f}. Driven by: {drivers}.",
                    link="/costs",
                )
            )
    if messages:
        org.settings = {**(org.settings or {}), "early_warning_state": state}
        db.flush()
        notify(db, "early_warning", messages)
    return len(messages)
