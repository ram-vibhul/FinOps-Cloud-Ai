"""Currency conversion with daily reference rates.

Rates are stored as units per 1 EUR for every calendar day. Converting an amount
from currency A to B on a day: ``amount * per_eur[B] / per_eur[A]``. In SQL this is
an expression: rows already in the display currency pass through untouched (the
common case costs nothing); other rows look up that day's two rates. If a rate is
missing the result is NULL, so the amount is left out of sums and reported as
"not converted" instead of being silently wrong.

Default provider: the European Central Bank's euro foreign exchange reference
rates (free, about 30 currencies including INR and USD, published on working days).
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import case, delete, func, insert, select
from sqlalchemy.orm import Session

from ccradar.db.base import Base
from ccradar.models import ExchangeRate, FxOverride

log = logging.getLogger(__name__)
ECB_90D = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml"
ECB_HISTORY = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.xml"
_DAY = re.compile(r"<Cube\s+time=[\"'](\d{4}-\d{2}-\d{2})[\"']\s*>(.*?)</Cube>", re.S)
_RATE = re.compile(r"currency=[\"']([A-Z]{3})[\"']\s+rate=[\"']([0-9.]+)[\"']")


def converted(amount: Any, currency_col: Any, day_col: Any, display: str, org_col: Any) -> Any:
    """SQL expression: ``amount`` converted to ``display`` currency (NULL if no rate).

    The row's organisation's override for that day wins (directly, or the inverse
    pair), then the daily reference rate."""
    rates = Base.metadata.tables["exchange_rates"]
    overrides = Base.metadata.tables["fx_overrides"]
    target = (
        select(rates.c.per_eur)
        .where(rates.c.day == day_col, rates.c.currency == display)
        .scalar_subquery()
    )
    source = (
        select(rates.c.per_eur)
        .where(rates.c.day == day_col, rates.c.currency == currency_col)
        .scalar_subquery()
    )

    def override(value: Any, from_: Any, to: Any) -> Any:
        return (
            select(value)
            .where(
                overrides.c.org_id == org_col,
                overrides.c.from_currency == from_,
                overrides.c.to_currency == to,
                overrides.c.start_day <= day_col,
                overrides.c.end_day >= day_col,
            )
            .limit(1)
            .scalar_subquery()
        )

    direct = override(overrides.c.rate, currency_col, display)
    inverse = override(1 / overrides.c.rate, display, currency_col)
    return case(
        (currency_col == display, amount),
        else_=amount * func.coalesce(direct, inverse, target / source),
    )


def parse_ecb(xml: str) -> dict[date, dict[str, Decimal]]:
    """Parse the ECB feed with a strict pattern (no XML parser: nothing to exploit)."""
    out: dict[date, dict[str, Decimal]] = {}
    for day, body in _DAY.findall(xml):
        rates = {cur: Decimal(rate) for cur, rate in _RATE.findall(body)}
        if rates:
            rates["EUR"] = Decimal(1)
            out[date.fromisoformat(day)] = rates
    return out


def store(
    db: Session, published: dict[date, dict[str, Decimal]], *, until: date, source: str = "ecb"
) -> int:
    """Upsert published days and fill every calendar day up to ``until`` by carrying
    the previous rate forward. Returns rows written."""
    if not published:
        return 0
    table = Base.metadata.tables["exchange_rates"]
    first = min(published)
    db.execute(delete(table).where(table.c.day >= first, table.c.day <= until))
    rows, current, last = [], first, published[first]
    while current <= until:
        todays = published.get(current)
        kind = source if todays else "carried"
        last = todays or last
        rows += [
            {"day": current, "currency": c, "per_eur": r, "source": kind} for c, r in last.items()
        ]
        current += timedelta(days=1)
    for i in range(0, len(rows), 5000):
        db.execute(insert(table), rows[i : i + 5000])
    db.flush()
    return len(rows)


def refresh(db: Session, http: httpx.Client, *, today: date, provider: str = "ecb") -> int:
    if provider != "ecb":
        return 0
    have_any = db.scalar(select(func.count()).select_from(ExchangeRate)) or 0
    url = ECB_90D if have_any else ECB_HISTORY  # first run: full history for old bills
    try:
        response = http.get(url, timeout=30.0)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("could not fetch exchange rates: %s", exc)
        return 0
    return store(db, parse_ecb(response.text), until=today)


def rate(
    db: Session, day: date, source: str, target: str, org_id: uuid.UUID | None = None
) -> Decimal | None:
    """Units of ``target`` per 1 ``source`` on ``day``: the organisation's override, else
    the daily reference rate."""
    if source == target:
        return Decimal(1)
    if org_id is not None:
        for from_, to, invert in ((source, target, False), (target, source, True)):
            found = db.scalar(
                select(FxOverride.rate).where(
                    FxOverride.org_id == org_id,
                    FxOverride.from_currency == from_,
                    FxOverride.to_currency == to,
                    FxOverride.start_day <= day,
                    FxOverride.end_day >= day,
                )
            )
            if found is not None:
                value = Decimal(str(found))
                return 1 / value if invert else value
    rows = dict(
        db.execute(
            select(ExchangeRate.currency, ExchangeRate.per_eur).where(
                ExchangeRate.day == day, ExchangeRate.currency.in_((source, target))
            )
        ).all()
    )
    if source not in rows or target not in rows:
        return None
    return Decimal(str(rows[target])) / Decimal(str(rows[source]))
