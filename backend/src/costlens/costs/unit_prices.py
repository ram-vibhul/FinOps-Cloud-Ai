"""Hourly prices per instance type, taken from the organisation's own bill.

Used by the change estimator (6.3) so estimates reflect what the customer actually pays
(after Savings Plans and other discounts in the effective cost). Covers EC2 instances,
RDS databases and ElastiCache nodes billed by the hour over the last 30 days."""

from __future__ import annotations

import re
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from costlens.costs.analytics import latest_date
from costlens.costs.fx import converted
from costlens.db.base import utcnow
from costlens.models import CostLineItem, Organization

# The instance type, written as in Terraform, from the end of the usage type.
PATTERNS = [
    re.compile(r"BoxUsage:([a-z0-9-]+\.[a-z0-9]+)$"),  # EC2 (Linux, shared tenancy)
    re.compile(r"InstanceUsage:(db\.[a-z0-9-]+\.[a-z0-9]+)$"),  # RDS single-AZ
    re.compile(r"NodeUsage:(cache\.[a-z0-9-]+\.[a-z0-9]+)$"),  # ElastiCache
]
MIN_HOURS = 24


def bill_prices(db: Session, org_id: uuid.UUID) -> dict[str, Any]:
    org = db.get(Organization, org_id)
    currency = org.default_currency if org else "USD"
    end = latest_date(db, org_id) or utcnow().date()
    t = CostLineItem
    amount = converted(t.effective_cost, t.billing_currency, t.charge_date, currency, t.org_id)
    rows = db.execute(
        select(t.usage_type, func.sum(amount), func.sum(t.consumed_quantity))
        .where(
            t.org_id == org_id,
            t.charge_date > end - timedelta(days=30),
            t.charge_date <= end,
            t.charge_category == "Usage",
            t.usage_type.is_not(None),
            (t.usage_type.like("%BoxUsage:%"))
            | (t.usage_type.like("%InstanceUsage:db.%"))
            | (t.usage_type.like("%NodeUsage:cache.%")),
        )
        .group_by(t.usage_type)
    ).all()
    totals: dict[str, list[float]] = {}
    for usage_type, cost, hours in rows:
        for pattern in PATTERNS:
            m = pattern.search(usage_type or "")
            if m and hours:
                acc = totals.setdefault(m.group(1), [0.0, 0.0])
                acc[0] += float(cost or 0)
                acc[1] += float(hours)
                break
    prices = {
        k: round(cost / hours, 6)
        for k, (cost, hours) in sorted(totals.items())
        if hours >= MIN_HOURS
    }
    return {"currency": currency, "as_of": end.isoformat(), "prices": prices}
