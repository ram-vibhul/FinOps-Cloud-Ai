"""Network and data-transfer costs from the bill's usage types.

The bill says what each kind of transfer costs and which resource or account paid it;
it does not say which traffic could take a cheaper path. That needs VPC Flow Logs
(item 4.21), so the matching findings are reviews with steps, not claimed savings."""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ccradar.costs.fx import converted
from ccradar.db.base import Base
from ccradar.models import Organization

CATEGORIES = {
    "nat_processing": "NAT gateway processing",
    "cross_az": "Between availability zones",
    "inter_region": "Between regions",
    "internet_out": "Out to the internet",
    "cdn_out": "CDN out to the internet",
    "transit_gateway": "Transit Gateway",
    "vpc_endpoint": "VPC endpoints",
}
_RULES = [
    ("nat_processing", re.compile(r"NatGateway-Bytes", re.I)),
    ("inter_region", re.compile(r"^[A-Z]{2,5}\d?-[A-Z]{2,5}\d?-AWS-(Out|In)-Bytes$", re.I)),
    ("cross_az", re.compile(r"DataTransfer-Regional-Bytes|DataTransfer-xAZ", re.I)),
    ("transit_gateway", re.compile(r"TransitGateway-Bytes", re.I)),
    ("vpc_endpoint", re.compile(r"VpcEndpoint-Bytes", re.I)),
    ("internet_out", re.compile(r"DataTransfer-Out-Bytes|DataTransfer-Out-OBytes", re.I)),
]


def classify(usage_type: str | None, service: str | None = None) -> str | None:
    if not usage_type:
        return None
    for key, pattern in _RULES:
        if pattern.search(usage_type):
            if key == "internet_out" and service and "CloudFront" in service:
                return "cdn_out"
            return key
    return None


def network_costs(
    db: Session,
    org_id: uuid.UUID,
    *,
    days: int = 30,
    currency: str | None = None,
    end: date | None = None,
) -> dict[str, Any]:
    t = Base.metadata.tables["cost_line_items"]
    org = db.get(Organization, org_id)
    currency = currency or (org.default_currency if org else "USD")
    last = end or db.scalar(select(func.max(t.c.charge_date)).where(t.c.org_id == org_id))
    empty = {
        "currency": currency,
        "days": days,
        "total": 0.0,
        "categories": [],
        "daily": {"periods": [], "series": []},
        "top": [],
    }
    if last is None:
        return empty
    start = last - timedelta(days=days - 1)
    amount = converted(
        t.c.effective_cost, t.c.billing_currency, t.c.charge_date, currency, t.c.org_id
    )
    rows = db.execute(
        select(
            t.c.charge_date,
            t.c.usage_type,
            t.c.service_name,
            t.c.sub_account_id,
            t.c.region,
            t.c.resource_id,
            func.sum(amount),
        )
        .where(
            t.c.org_id == org_id,
            t.c.charge_date >= start,
            t.c.charge_date <= last,
            t.c.usage_type.ilike("%Bytes%"),
        )
        .group_by(
            t.c.charge_date,
            t.c.usage_type,
            t.c.service_name,
            t.c.sub_account_id,
            t.c.region,
            t.c.resource_id,
        )
    ).all()
    by_cat: dict[str, float] = defaultdict(float)
    by_day: dict[str, dict[date, float]] = defaultdict(lambda: defaultdict(float))
    top: dict[tuple, float] = defaultdict(float)
    for day, usage, service, account, region, resource, cost in rows:
        cat = classify(usage, service)
        if cat is None or cost is None:
            continue
        value = float(cost)
        by_cat[cat] += value
        by_day[cat][day] += value
        top[(cat, resource, service, account, region)] += value
    total = sum(by_cat.values())
    periods = [start + timedelta(days=i) for i in range((last - start).days + 1)]
    ordered = sorted(by_cat, key=lambda k: -by_cat[k])
    return {
        "currency": currency,
        "days": days,
        "total": round(total, 2),
        "start": start.isoformat(),
        "end": last.isoformat(),
        "categories": [
            {
                "key": k,
                "label": CATEGORIES[k],
                "cost": round(by_cat[k], 2),
                "share_pct": round(100 * by_cat[k] / total, 1) if total else None,
            }
            for k in ordered
        ],
        "daily": {
            "periods": [p.isoformat() for p in periods],
            "series": [
                {
                    "key": k,
                    "label": CATEGORIES[k],
                    "total": round(by_cat[k], 2),
                    "values": [round(by_day[k].get(p, 0.0), 2) for p in periods],
                }
                for k in ordered
            ],
        },
        "top": [
            {
                "category": cat,
                "label": CATEGORIES[cat],
                "resource_id": resource,
                "service": service,
                "account": account,
                "region": region,
                "cost": round(v, 2),
            }
            for (cat, resource, service, account, region), v in sorted(
                top.items(), key=lambda kv: -kv[1]
            )[:25]
        ],
    }
