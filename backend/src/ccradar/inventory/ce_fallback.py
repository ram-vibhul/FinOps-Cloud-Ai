"""Cost Explorer resource-level fallback (EC2, last 14 days) for accounts without CUR.

Needs "resource-level data" enabled in the customer's Cost Explorer settings and costs
USD 0.01 per request, so it is opt-in per connection (``settings.ce_resource_level``).
Results are kept on the inventory row (``attributes.cost_14d_ce``), never in the cost
tables, so nothing is counted twice.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ccradar.models import CloudResource


def fetch(ce: Any, today: date) -> dict[str, float]:
    start, end = today - timedelta(days=14), today
    totals: dict[str, float] = {}
    token = None
    for _ in range(20):
        args: dict[str, Any] = {
            "TimePeriod": {"Start": start.isoformat(), "End": end.isoformat()},
            "Granularity": "DAILY",
            "Metrics": ["AmortizedCost"],
            "Filter": {
                "Dimensions": {
                    "Key": "SERVICE",
                    "Values": ["Amazon Elastic Compute Cloud - Compute"],
                }
            },
            "GroupBy": [{"Type": "DIMENSION", "Key": "RESOURCE_ID"}],
        }
        if token:
            args["NextPageToken"] = token
        page = ce.get_cost_and_usage_with_resources(**args)
        for result in page.get("ResultsByTime", []):
            for group in result.get("Groups", []):
                rid = group["Keys"][0]
                totals[rid] = totals.get(rid, 0.0) + float(
                    group["Metrics"]["AmortizedCost"]["Amount"]
                )
        token = page.get("NextPageToken")
        if not token:
            break
    return totals


def apply(db: Session, totals: dict[str, float]) -> int:
    rows = db.scalars(
        select(CloudResource).where(CloudResource.resource_id.in_(list(totals)))
    ).all()
    for row in rows:
        row.attributes = {
            **(row.attributes or {}),
            "cost_14d_ce": round(totals[row.resource_id], 2),
        }
    db.flush()
    return len(rows)
