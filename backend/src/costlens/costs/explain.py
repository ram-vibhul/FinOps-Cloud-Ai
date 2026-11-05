"""Explain an anomaly in plain words: what moved, by how much, the likely driver and any
change made just before. Template-based; optional AI rewording on top."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from costlens.db.base import Base
from costlens.models import CloudAccount, CostAnomaly

BASELINE_DAYS = 14
TOP = 3
# What to break an anomaly down by, per anomaly dimension.
BREAKDOWNS = {
    "service": ("sub_account_id", "region", "usage_type", "resource_id"),
    "account": ("service_name", "region", "usage_type", "resource_id"),
    "total": ("service_name", "sub_account_id", "region"),
}
NOUNS = {
    "service_name": "service",
    "sub_account_id": "account",
    "region": "region",
    "usage_type": "usage type",
    "resource_id": "resource",
}


def _money(value: float, currency: str) -> str:
    return f"{value:,.2f} {currency}"


def _day(d: date) -> str:
    return f"{d.day} {d:%b}"


def _scope(table: Any, a: CostAnomaly) -> list[Any]:
    where = [table.c.org_id == a.org_id, table.c.billing_currency == a.currency]
    if a.dimension == "service":
        where.append(table.c.service_name == a.key)
    elif a.dimension == "account":
        where.append(table.c.sub_account_id == a.key)
    return where


def drivers(db: Session, a: CostAnomaly) -> list[dict[str, Any]]:
    """Per breakdown, the values whose daily spend rose most versus the 14 days before."""
    table = Base.metadata.tables["cost_line_items"]
    where = _scope(table, a)
    episode_days = (a.last_day - a.day).days + 1
    base_start = a.day - timedelta(days=BASELINE_DAYS)
    out = []
    for column in BREAKDOWNS.get(a.dimension, ()):
        col = table.c[column]

        def per_value(start: date, end: date, col: Any = col) -> dict[str | None, float]:
            rows = db.execute(
                select(col, func.sum(table.c.effective_cost))
                .where(*where, table.c.charge_date >= start, table.c.charge_date <= end)
                .group_by(col)
            ).all()
            return {k: float(v or 0) for k, v in rows}

        during = per_value(a.day, a.last_day)
        before = per_value(base_start, a.day - timedelta(days=1))
        moves = []
        for value, spent in during.items():
            if value in (None, ""):
                continue
            now = spent / episode_days
            usual = before.get(value, 0.0) / BASELINE_DAYS
            if now - usual > 0:
                moves.append(
                    {
                        "by": column,
                        "value": value,
                        "per_day": round(now, 2),
                        "usual_per_day": round(usual, 2),
                        "increase_per_day": round(now - usual, 2),
                        "new": usual == 0,
                    }
                )
        moves.sort(key=lambda m: m["increase_per_day"], reverse=True)
        out.extend(moves[:TOP])
    return out


def _name(db: Session, by: str, value: str) -> str:
    if by == "sub_account_id":
        name = db.scalar(select(CloudAccount.name).where(CloudAccount.external_id == value))
        return f"{name} ({value})" if name else value
    return value


def explain(db: Session, a: CostAnomaly) -> dict[str, Any]:
    from costlens.changes.service import related_events

    what = (
        "All spend"
        if a.dimension == "total"
        else _name(db, "sub_account_id" if a.dimension == "account" else "service_name", a.key)
    )
    pct = f" (+{(a.actual - a.expected) / a.expected * 100:.0f}%)" if a.expected > 0 else ""
    if a.days > 1:
        when = f"from {_day(a.day)} to {_day(a.last_day)} ({a.days} days)"
        lead = (
            f"{what} has cost more than usual {when}: about "
            f"{_money(a.impact, a.currency)} extra in total. On {_day(a.last_day)} it was "
            f"{_money(a.actual, a.currency)} against a usual {_money(a.expected, a.currency)}{pct}."
        )
    else:
        lead = (
            f"{what} cost {_money(a.actual, a.currency)} on {_day(a.day)}, about "
            f"{_money(a.actual - a.expected, a.currency)} more than the usual "
            f"{_money(a.expected, a.currency)}{pct}."
        )
    found = drivers(db, a)
    sentences = [lead]
    if found:
        # The biggest mover among the broadest breakdown, then detail from narrower ones.
        first_by = BREAKDOWNS[a.dimension][0]
        main = next((d for d in found if d["by"] == first_by), found[0])
        share = main["increase_per_day"] / max(a.actual - a.expected, 0.01)
        how_much = "Most" if share >= 0.6 else "Much" if share >= 0.3 else "Some"
        text = (
            f"{how_much} of the increase came from {NOUNS[main['by']]} "
            f"{_name(db, main['by'], main['value'])} "
            f"(+{_money(main['increase_per_day'], a.currency)} a day"
            f"{', new spend' if main['new'] else ''})"
        )
        detail = [
            d for d in found if d["by"] in ("usage_type", "resource_id") and d["by"] != main["by"]
        ]
        if detail:
            d = detail[0]
            text += f", mainly {NOUNS[d['by']]} {d['value']}"
        sentences.append(text + ".")
    events = related_events(db, a)
    if events:
        e = events[0]
        sentences.append(
            f'A change shortly before may explain it: "{e.title}" ({e.source}, '
            f"{_day(e.occurred_at.date())})."
        )
    elif a.status == "open":
        sentences.append("No recorded change matches it yet.")
    return {
        "text": " ".join(sentences),
        "drivers": found,
        "changes": [
            {
                "id": str(e.id),
                "title": e.title,
                "source": e.source,
                "occurred_at": e.occurred_at.isoformat(),
                "url": e.url,
            }
            for e in events
        ],
        "worded_by": "template",
    }
