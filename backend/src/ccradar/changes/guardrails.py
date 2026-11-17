"""Budget guardrails for live changes (11.33).

A budget's forecast is built from the last two weeks of the bill, so a server launched
an hour ago is not in it yet. When Radar prices a new change, this adds the change's
cost for the rest of the budget period to that forecast and warns when the change is
what pushes the budget over.

A change is matched to a budget only on what both know: provider, account, region and
service. A budget filtered on anything else (tags, teams, usage types) cannot be matched
from a CloudTrail record and is skipped rather than guessed."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ccradar.costs import fx
from ccradar.costs.budgets import _chain, effective_filters, period_bounds, status
from ccradar.costs.explorer import tag_aliases
from ccradar.models import Budget, ChangeEvent, Organization

DAYS_IN_MONTH = 30.4


def _dimensions(e: ChangeEvent) -> dict[str, str | None]:
    return {"provider": "aws", "account": e.account, "region": e.region, "service": e.service}


def applies(filters: dict[str, list[str]], e: ChangeEvent) -> bool:
    dims = _dimensions(e)
    for dim, values in filters.items():
        if dim not in dims or dims[dim] is None or dims[dim] not in values:
            return False
    return True


def pushed_over(
    db: Session, org_id: uuid.UUID, events: list[ChangeEvent], today: date
) -> list[dict[str, Any]]:
    """Budgets that a forecast already under the amount would now exceed because of
    ``events``: one entry per budget, with the changes responsible."""
    costly = [
        e for e in events if float(((e.attributes or {}).get("impact") or {}).get("monthly", 0)) > 0
    ]
    if not costly:
        return []
    budgets = list(db.scalars(select(Budget)))
    by_id = {b.id: b for b in budgets}
    org = db.get(Organization, org_id)
    aliases = tag_aliases(org)
    out = []
    for b in budgets:
        filters = effective_filters(_chain(b, by_id), aliases)
        if filters is None or not b.amount:
            continue
        mine = [e for e in costly if applies(filters, e)]
        if not mine:
            continue
        _, end, _ = period_bounds(b.period, today)
        days_left = max((end - today).days + 1, 0)
        extra = 0.0
        for e in mine:
            impact = e.attributes["impact"]
            rate = fx.rate(db, today, str(impact.get("currency") or "USD"), b.currency, org_id)
            if rate is None:
                continue
            extra += float(impact["monthly"]) * float(rate) * days_left / DAYS_IN_MONTH
        if extra <= 0:
            continue
        current = status(db, org_id, b, by_id, today)
        before, after = float(current["forecast"]), float(current["forecast"]) + extra
        if before <= b.amount < after:
            out.append(
                {
                    "budget": b,
                    "forecast_before": round(before, 2),
                    "forecast_after": round(after, 2),
                    "extra": round(extra, 2),
                    "changes": mine,
                }
            )
    return out


def warnings(db: Session, org_id: uuid.UUID, events: list[ChangeEvent], today: date) -> list[Any]:
    """Notification messages for :func:`pushed_over`."""
    from ccradar.changes.radar import short_actor
    from ccradar.core.notify import Message

    messages = []
    for hit in pushed_over(db, org_id, events, today):
        b = hit["budget"]
        who = sorted({short_actor(e.actor) for e in hit["changes"]})
        first = hit["changes"][0]
        messages.append(
            Message(
                title=f"Budget {b.name} will go over: forecast now {hit['forecast_after']:,.0f} "
                f"of {b.amount:,.0f} {b.currency}"[:200],
                text=(
                    f"Changes just made by {', '.join(who)} add about {hit['extra']:,.0f} "
                    f"{b.currency} before the period ends; the forecast was "
                    f"{hit['forecast_before']:,.0f}, within the budget."
                ),
                link=f"/radar?change={first.id}",
                actions=[("Open the budget", "/governance?tab=budgets")],
            )
        )
    return messages
