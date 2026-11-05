"""Import daily costs from AWS Cost Explorer.

One query per billing period: DAILY granularity grouped by linked account and
service, metrics UnblendedCost (billed) and AmortizedCost (effective). Cost
Explorer charges about USD 0.01 per request, so we fetch whole months, follow
pagination only as needed, and run every six hours.

Cost Explorer has no region or resource detail; connect the CUR 2.0 export (2.4)
for that. Connections with a CUR bucket skip this import to avoid double counting.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from costlens.clouds.aws.client import AwsAccessError, AwsGateway
from costlens.costs.ingest import CostLine, month_start, replace_billing_period
from costlens.db.base import utcnow
from costlens.db.tenant import set_tenant
from costlens.models import CloudAccount, CloudConnection, CostImportRun

log = logging.getLogger(__name__)
MAX_PAGES = 50


def classify(service: str, amount: Decimal) -> tuple[str, str | None]:
    """FOCUS charge and pricing category from a Cost Explorer service name."""
    lowered = service.lower()
    if lowered == "tax":
        return "Tax", None
    if amount < 0:
        return "Credit", None
    if "savings plans" in lowered or lowered.startswith("reserved"):
        return "Purchase", "Committed"
    return "Usage", "Standard"


def _pages(ce: Any, request: dict[str, Any]) -> Iterator[dict[str, Any]]:
    token = None
    for _ in range(MAX_PAGES):
        args = dict(request)
        if token:
            args["NextPageToken"] = token
        page = ce.get_cost_and_usage(**args)
        yield page
        token = page.get("NextPageToken")
        if not token:
            return
    log.warning("Cost Explorer pagination stopped after %s pages", MAX_PAGES)


def fetch_period(ce: Any, *, start: date, end: date) -> tuple[list[CostLine], dict[str, str]]:
    """Lines for [start, end) and linked account names seen."""
    request = {
        "TimePeriod": {"Start": start.isoformat(), "End": end.isoformat()},
        "Granularity": "DAILY",
        "Metrics": ["UnblendedCost", "AmortizedCost"],
        "GroupBy": [
            {"Type": "DIMENSION", "Key": "LINKED_ACCOUNT"},
            {"Type": "DIMENSION", "Key": "SERVICE"},
        ],
    }
    lines: list[CostLine] = []
    names: dict[str, str] = {}
    for page in _pages(ce, request):
        for attr in page.get("DimensionValueAttributes", []):
            if attr.get("Value") and attr.get("Attributes", {}).get("description"):
                names[attr["Value"]] = attr["Attributes"]["description"]
        for result in page.get("ResultsByTime", []):
            day = date.fromisoformat(result["TimePeriod"]["Start"])
            for group in result.get("Groups", []):
                account, service = group["Keys"]
                metrics = group["Metrics"]
                billed = Decimal(metrics["UnblendedCost"]["Amount"])
                effective = Decimal(metrics["AmortizedCost"]["Amount"])
                if billed == 0 and effective == 0:
                    continue
                charge, pricing = classify(service, billed)
                lines.append(
                    CostLine(
                        charge_date=day,
                        provider="aws",
                        sub_account_id=account,
                        sub_account_name=names.get(account),
                        service_name=service,
                        billed_cost=billed,
                        effective_cost=effective,
                        list_cost=None,
                        billing_currency=metrics["UnblendedCost"].get("Unit", "USD"),
                        charge_category=charge,
                        pricing_category=pricing,
                        extras={"estimated": bool(result.get("Estimated"))},
                    )
                )
    for line in lines:
        line.sub_account_name = names.get(line.sub_account_id or "", line.sub_account_name)
    return lines, names


def periods(today: date, months: int) -> list[tuple[date, date]]:
    """[start, end) per billing month, newest last; the current month ends tomorrow."""
    result = []
    current = month_start(today)
    for _ in range(months):
        nxt = (current + timedelta(days=32)).replace(day=1)
        result.append((current, min(nxt, today + timedelta(days=1))))
        current = (current - timedelta(days=1)).replace(day=1)
    return list(reversed(result))


def import_connection(
    db: Session,
    gateway: AwsGateway,
    connection_id: Any,
    *,
    months: int = 2,
    today: date | None = None,
) -> list[CostImportRun]:
    set_tenant(db, bypass=True)
    conn = db.get(CloudConnection, connection_id)
    if conn is None or conn.status != "active" or not conn.role_arn:
        return []
    org_id = conn.org_id
    today = today or utcnow().date()
    try:
        session = gateway.assume(conn.role_arn, conn.external_id, f"costlens-{org_id}")
        ce = gateway.client(session, "ce", region="us-east-1")
    except AwsAccessError as exc:
        conn.status, conn.last_error = "error", str(exc)
        db.flush()
        return []
    runs = []
    for start, end in periods(today, months):
        lines, names = fetch_period(ce, start=start, end=end)
        runs.append(
            replace_billing_period(
                db,
                org_id=org_id,
                source_kind="aws_ce",
                source_key=f"aws_ce:{conn.id}",
                billing_period=start,
                lines=lines,
            )
        )
        _remember_accounts(db, org_id, conn, names)
    set_tenant(db, bypass=True)
    conn.last_checked_at, conn.last_error = utcnow(), None
    db.flush()
    return runs


def _remember_accounts(
    db: Session, org_id: Any, conn: CloudConnection, names: dict[str, str]
) -> None:
    set_tenant(db, org_id=org_id)
    for account_id, name in names.items():
        existing = db.scalar(
            select(CloudAccount).where(
                CloudAccount.provider == "aws", CloudAccount.external_id == account_id
            )
        )
        if existing is None:
            db.add(
                CloudAccount(
                    org_id=org_id,
                    provider="aws",
                    external_id=account_id,
                    name=name,
                    parent_id=conn.cloud_account_id if account_id != conn.account_id else None,
                )
            )
        elif not existing.name:
            existing.name = name
    db.flush()
