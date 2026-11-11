"""Import Azure costs, read-only.

Two ways, like AWS:
- **Cost Management Query API** (default): daily cost grouped by subscription and
  service, once as actual cost (billed) and once as amortised cost (effective). No
  resource detail. Needs the Cost Management Reader role on the scope.
- **FOCUS export** (when a storage account is set): the files of a Cost Management
  export in FOCUS format, with resource, region, tags and commitment detail. Needs
  Storage Blob Data Reader on the container. When set, the Query API import is skipped
  so nothing is counted twice.
"""

from __future__ import annotations

import csv
import gzip
import io
import logging
import re
import uuid
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from ccradar.clouds.aws.cost_explorer import periods
from ccradar.clouds.azure.client import AzureClient, AzureError
from ccradar.costs import focus
from ccradar.costs.ingest import CostLine, replace_billing_period
from ccradar.db.base import utcnow
from ccradar.db.tenant import set_tenant
from ccradar.models import CloudConnection, CostImportRun

log = logging.getLogger(__name__)
API = "2023-11-01"
SCOPE = re.compile(
    r"^/(subscriptions/[0-9a-fA-F-]{36}"
    r"|providers/Microsoft\.Billing/billingAccounts/[\w:\-]+"
    r"|providers/Microsoft\.Management/managementGroups/[\w.\-()]+)$"
)
RANGE = re.compile(r"/(\d{8})-(\d{8})/")
MAX_PAGES = 50


def _query(
    client: AzureClient, scope: str, kind: str, start: date, end: date
) -> dict[tuple, Decimal]:
    """{(day, subscription, service, currency): cost} for [start, end)."""
    body = {
        "type": kind,
        "timeframe": "Custom",
        "timePeriod": {
            "from": f"{start.isoformat()}T00:00:00Z",
            "to": f"{(end - timedelta(days=1)).isoformat()}T23:59:59Z",
        },
        "dataset": {
            "granularity": "Daily",
            "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
            "grouping": [
                {"type": "Dimension", "name": "SubscriptionId"},
                {"type": "Dimension", "name": "ServiceName"},
            ],
        },
    }
    out: dict[tuple, Decimal] = defaultdict(Decimal)
    url = f"{scope}/providers/Microsoft.CostManagement/query?api-version={API}"
    for _ in range(MAX_PAGES):
        props = client.request("POST", url, json=body).json().get("properties") or {}
        cols = [c["name"].lower() for c in props.get("columns") or []]
        for row in props.get("rows") or []:
            r = dict(zip(cols, row, strict=False))
            day = datetime.strptime(str(r.get("usagedate")), "%Y%m%d").date()
            key = (
                day,
                r.get("subscriptionid") or "",
                r.get("servicename") or "Other",
                r.get("currency") or "USD",
            )
            out[key] += Decimal(str(r.get("cost") or r.get("costusd") or 0))
        url = props.get("nextLink")
        if not url:
            break
    return out


def fetch_period(client: AzureClient, scope: str, start: date, end: date) -> list[CostLine]:
    actual = _query(client, scope, "ActualCost", start, end)
    amortized = _query(client, scope, "AmortizedCost", start, end)
    lines = []
    for key in sorted(set(actual) | set(amortized)):
        day, sub, service, currency = key
        billed, effective = actual.get(key, Decimal(0)), amortized.get(key, Decimal(0))
        if billed == 0 and effective == 0:
            continue
        purchase = billed > 0 and effective == 0  # e.g. a reservation bought up front
        lines.append(
            CostLine(
                charge_date=day,
                provider="azure",
                sub_account_id=sub or None,
                service_name=service,
                billed_cost=billed,
                effective_cost=effective,
                billing_currency=currency.upper()[:3],
                charge_category="Credit" if billed < 0 else "Purchase" if purchase else "Usage",
                pricing_category=None if billed < 0 else "Committed" if purchase else "Standard",
            )
        )
    return lines


def _blobs(client: AzureClient, account: str, container: str, prefix: str) -> list[tuple[str, str]]:
    """(name, last modified) of every blob under ``prefix``."""
    out: list[tuple[str, str]] = []
    marker = ""
    for _ in range(MAX_PAGES):
        params = {"restype": "container", "comp": "list", "prefix": prefix}
        if marker:
            params["marker"] = marker
        root = ET.fromstring(client.blob(account, container, **params).content)  # noqa: S314 - Azure's own XML
        for b in root.iter("Blob"):
            name = b.findtext("Name") or ""
            out.append((name, b.findtext("Properties/Last-Modified") or ""))
        marker = root.findtext("NextMarker") or ""
        if not marker:
            break
    return out


def _rows(name: str, data: bytes) -> list[dict[str, Any]]:
    if name.endswith(".parquet"):
        import pyarrow.parquet as pq

        return pq.read_table(io.BytesIO(data)).to_pylist()
    if name.endswith(".gz"):
        data = gzip.decompress(data)
    return list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))


def focus_period(
    client: AzureClient, settings: dict[str, Any], month: date
) -> list[CostLine] | None:
    """Lines from the newest export run covering ``month``; None when there is none."""
    account, container = settings["storage_account"], settings["container"]
    prefix = (settings.get("prefix") or "").strip("/")
    blobs = _blobs(client, account, container, f"{prefix}/" if prefix else "")
    runs: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for name, modified in blobs:
        m = RANGE.search(f"/{name}")
        if not m or not re.search(r"\.(csv|csv\.gz|parquet)$", name):
            continue
        if datetime.strptime(m.group(1), "%Y%m%d").date().replace(day=1) != month:
            continue
        run = name.rsplit("/", 1)[0]  # .../<range>/<run id>
        runs[run].append((name, modified))
    if not runs:
        return None

    def newest(item: tuple[str, list[tuple[str, str]]]) -> datetime:
        return max(_http_date(mod) for _, mod in item[1])

    _, files = max(runs.items(), key=newest)
    lines: list[CostLine] = []
    for name, _ in sorted(files):
        for row in _rows(name, client.blob(account, container, name).content):
            if not row.get("ChargePeriodStart"):
                continue
            line = focus.row_to_line(row, default_provider="azure")
            line.provider = "azure"
            lines.append(line)
    return lines


def _http_date(text: str) -> datetime:
    try:
        return datetime.strptime(text, "%a, %d %b %Y %H:%M:%S %Z")
    except ValueError:
        return datetime.min


def import_connection(
    db: Session,
    client: AzureClient,
    connection_id: uuid.UUID,
    *,
    months: int = 2,
    today: date | None = None,
) -> list[CostImportRun]:
    set_tenant(db, bypass=True)
    conn = db.get(CloudConnection, connection_id)
    if conn is None or conn.provider != "azure" or conn.status != "active":
        return []
    cfg = conn.settings or {}
    org_id, today = conn.org_id, today or utcnow().date()
    use_focus = bool(cfg.get("storage_account") and cfg.get("container"))
    runs = []
    try:
        for start, end in periods(today, months):
            if use_focus:
                lines = focus_period(client, cfg, start)
                if lines is None:
                    continue
                kind = "azure_focus"
            else:
                lines, kind = (
                    fetch_period(client, cfg["scope"], start, end),
                    "azure_cost_management",
                )
            runs.append(
                replace_billing_period(
                    db,
                    org_id=org_id,
                    source_kind=kind,
                    source_key=f"azure:{conn.id}",
                    billing_period=start,
                    lines=lines,
                )
            )
    except AzureError as exc:
        set_tenant(db, bypass=True)
        conn.last_error = str(exc)[:1000]
        if exc.status in (401, 403):
            conn.status = "error"
        db.flush()
        return runs
    set_tenant(db, bypass=True)
    conn.last_checked_at, conn.last_error = utcnow(), None
    db.flush()
    return runs
