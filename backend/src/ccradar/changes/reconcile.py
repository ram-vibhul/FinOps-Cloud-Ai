"""Radar reconciliation (11.27): estimate against the bill.

Radar prices a change minutes after it happens. Once billing data covers full days after
the change, this measures what the resource it created or resized actually cost per day,
turns that into a monthly run rate, and stores it next to the estimate. The accuracy of
all measured changes is published on the Radar page, so customers can see how far to
trust an estimate.

Only changes that added cost and name one resource are measured (a launch of several
servers is measured on its first one and compared with the per-server estimate).
Removals cannot be measured this way: what is gone has no lines.
"""

from __future__ import annotations

import re
import statistics
import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ccradar.costs.analytics import latest_date
from ccradar.db.base import Base
from ccradar.models import ChangeEvent

DAYS_IN_MONTH = 30.4
MIN_DAYS = 2  # full billed days after the change before measuring
MAX_DAYS = 7
COUNT = re.compile(r"^(\d+) x ")
WITHIN = 25.0  # percent: "close" for the published accuracy


def _per_resource(impact: dict[str, Any]) -> float:
    monthly = float(impact.get("monthly") or 0)
    m = COUNT.match(str(impact.get("label") or ""))
    return monthly / int(m[1]) if m and int(m[1]) > 1 else monthly


def measure(db: Session, org_id: uuid.UUID, e: ChangeEvent, through: date) -> dict | None:
    impact = (e.attributes or {}).get("impact") or {}
    if not e.resource_id or float(impact.get("monthly") or 0) <= 0:
        return None
    first = e.occurred_at.date() + timedelta(days=1)  # the change day is only partly billed
    last = min(first + timedelta(days=MAX_DAYS - 1), through)
    days = (last - first).days + 1
    if days < MIN_DAYS:
        return None
    li = Base.metadata.tables["cost_line_items"]
    rid = e.resource_id
    total = db.scalar(
        select(func.sum(li.c.effective_cost)).where(
            li.c.org_id == org_id,
            li.c.charge_date >= first,
            li.c.charge_date <= last,
            li.c.billing_currency == str(impact.get("currency") or "USD"),
            or_(
                li.c.resource_id == rid,
                li.c.resource_id.like(f"%/{rid}"),
                li.c.resource_id.like(f"%:{rid}"),
            ),
        )
    )
    if total is None:
        return None  # not in the bill (other currency, resource-level data missing…)
    measured = float(total) / days * DAYS_IN_MONTH
    estimate = _per_resource(impact)
    return {
        "monthly": round(measured, 2),
        "estimate": round(estimate, 2),
        "error_pct": round((measured - estimate) / estimate * 100, 1) if estimate else None,
        "days": days,
        "through": last.isoformat(),
    }


def reconcile(db: Session, org_id: uuid.UUID) -> int:
    """Measure changes whose days after are now billed; returns how many were measured."""
    through = latest_date(db, org_id)
    if through is None:
        return 0
    since = through - timedelta(days=60)
    measured = 0
    for e in db.scalars(
        select(ChangeEvent).where(
            ChangeEvent.source == "cloudtrail",
            ChangeEvent.resource_id.is_not(None),
            ChangeEvent.occurred_at >= since,
        )
    ):
        attributes = dict(e.attributes or {})
        old = attributes.get("measured") or {}
        if old.get("days", 0) >= MAX_DAYS:
            continue
        result = measure(db, org_id, e, through)
        if result is None or result["days"] <= old.get("days", 0):
            continue
        attributes["measured"] = result
        e.attributes = attributes
        measured += 1
    db.flush()
    return measured


def accuracy(db: Session) -> dict[str, Any] | None:
    """How close the estimates were, over every change measured so far."""
    errors = []
    rows = db.scalars(select(ChangeEvent.attributes).where(ChangeEvent.source == "cloudtrail"))
    for attributes in rows:
        m = (attributes or {}).get("measured") or {}
        if m.get("error_pct") is not None:
            errors.append(abs(float(m["error_pct"])))
    if not errors:
        return None
    return {
        "measured": len(errors),
        "median_error_pct": round(statistics.median(errors), 1),
        "within_pct": WITHIN,
        "share_within": round(100 * sum(e <= WITHIN for e in errors) / len(errors), 1),
    }
