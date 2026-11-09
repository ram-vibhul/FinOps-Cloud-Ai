"""Import Google Cloud costs from the Cloud Billing export to BigQuery (read-only).

One query per invoice month, grouped by day, project, service, SKU, region, resource
(detailed export only) and labels. Credits (CUDs, sustained use, promotions) are
included, so billed and effective cost are what the invoice shows. BigQuery bills the
customer's project for the bytes each query scans; filtering on usage time keeps that
small on partitioned export tables.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from costlens.clouds.aws.cost_explorer import periods
from costlens.clouds.gcp.client import GcpClient, GcpError
from costlens.costs.ingest import CostLine, replace_billing_period
from costlens.db.base import utcnow
from costlens.db.tenant import set_tenant
from costlens.models import CloudConnection, CostImportRun

log = logging.getLogger(__name__)
TABLE = re.compile(r"^[a-z][a-z0-9-]{4,29}\.[A-Za-z0-9_]{1,1024}\.[A-Za-z0-9_]{1,1024}$")
PROJECT = re.compile(r"^[a-z][a-z0-9-]{4,29}$")
MAX_PAGES = 200
CHARGE = {
    "regular": "Usage",
    "tax": "Tax",
    "adjustment": "Adjustment",
    "rounding_error": "Adjustment",
}

SQL = """
SELECT
  DATE(usage_start_time) AS day,
  project.id AS project_id,
  ANY_VALUE(project.name) AS project_name,
  service.description AS service,
  sku.description AS sku,
  sku.id AS sku_id,
  location.region AS region,
  location.zone AS zone,
  {resource} AS resource,
  cost_type,
  currency,
  TO_JSON_STRING(labels) AS labels,
  SUM(cost) AS cost,
  SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) AS c), 0)) AS credits,
  SUM(usage.amount_in_pricing_units) AS quantity,
  ANY_VALUE(usage.pricing_unit) AS unit
FROM `{table}`
WHERE invoice.month = @month
  AND usage_start_time >= TIMESTAMP(@start)
  AND usage_start_time < TIMESTAMP(@end)
GROUP BY day, project_id, service, sku, sku_id, region, zone, resource, cost_type, currency, labels
"""


def build_sql(table: str, detailed: bool) -> str:
    if not TABLE.match(table):
        raise GcpError("the table must look like project.dataset.table")
    return SQL.format(table=table, resource="resource.name" if detailed else "CAST(NULL AS STRING)")


def _params(month: date) -> list[dict[str, Any]]:
    nxt = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    values = {
        "month": month.strftime("%Y%m"),
        "start": (
            month - timedelta(days=3)
        ).isoformat(),  # late-arriving usage is billed in the month
        "end": (nxt + timedelta(days=3)).isoformat(),
    }
    return [
        {"name": k, "parameterType": {"type": "STRING"}, "parameterValue": {"value": v}}
        for k, v in values.items()
    ]


def query(
    client: GcpClient, project: str, sql: str, params: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Run a query and return all rows as dicts (waits for the job and follows pages)."""
    base = f"https://bigquery.googleapis.com/bigquery/v2/projects/{project}/queries"
    body = client.request(
        "POST",
        base,
        json={
            "query": sql,
            "useLegacySql": False,
            "parameterMode": "NAMED",
            "queryParameters": params,
            "timeoutMs": 30000,
            "maxResults": 10000,
        },
    )
    job = body.get("jobReference") or {}
    rows: list[dict[str, Any]] = []
    for _ in range(MAX_PAGES):
        if body.get("jobComplete"):
            names = [f["name"] for f in (body.get("schema") or {}).get("fields") or []]
            for r in body.get("rows") or []:
                rows.append({n: c.get("v") for n, c in zip(names, r.get("f") or [], strict=False)})
            token = body.get("pageToken")
            if not token:
                return rows
        else:
            token = None
        params_q: dict[str, Any] = {"timeoutMs": 30000, "maxResults": 10000}
        if job.get("location"):
            params_q["location"] = job["location"]
        if token:
            params_q["pageToken"] = token
        body = client.request("GET", f"{base}/{job.get('jobId')}", params=params_q)
    raise GcpError("the billing query did not finish in time")


def _line(r: dict[str, Any]) -> CostLine | None:
    cost = Decimal(str(r.get("cost") or 0)) + Decimal(str(r.get("credits") or 0))
    if cost == 0:
        return None
    sku = r.get("sku") or ""
    commitment = "commitment" in sku.lower() and cost > 0
    charge = CHARGE.get(r.get("cost_type") or "regular", "Usage")
    if charge == "Usage" and cost < 0:
        charge = "Credit"
    tags: dict[str, str] = {}
    try:
        for item in json.loads(r.get("labels") or "[]") or []:
            if item.get("key"):
                tags[str(item["key"])] = str(item.get("value") or "")
    except ValueError:
        pass
    qty = r.get("quantity")
    return CostLine(
        charge_date=date.fromisoformat(r["day"]),
        provider="gcp",
        billing_account_id=None,
        sub_account_id=r.get("project_id") or None,
        sub_account_name=r.get("project_name") or None,
        service_name=r.get("service") or "Other",
        sku_id=r.get("sku_id") or None,
        usage_type=sku or None,
        region=r.get("region") or None,
        availability_zone=r.get("zone") or None,
        resource_id=r.get("resource") or None,
        billed_cost=cost,
        effective_cost=cost,
        billing_currency=(r.get("currency") or "USD").upper()[:3],
        charge_category="Purchase" if commitment and charge == "Usage" else charge,
        pricing_category="Committed" if commitment else ("Standard" if charge == "Usage" else None),
        consumed_quantity=Decimal(str(qty)) if qty not in (None, "") else None,
        consumed_unit=r.get("unit") or None,
        tags=tags,
    )


def fetch_month(client: GcpClient, cfg: dict[str, Any], month: date) -> list[CostLine]:
    sql = build_sql(cfg["table"], bool(cfg.get("detailed")))
    rows = query(client, cfg["query_project"], sql, _params(month))
    return [line for line in (_line(r) for r in rows) if line is not None]


def import_connection(
    db: Session,
    client: GcpClient,
    connection_id: uuid.UUID,
    *,
    months: int = 2,
    today: date | None = None,
) -> list[CostImportRun]:
    set_tenant(db, bypass=True)
    conn = db.get(CloudConnection, connection_id)
    if conn is None or conn.provider != "gcp" or conn.status != "active":
        return []
    cfg, org_id, today = conn.settings or {}, conn.org_id, today or utcnow().date()
    runs = []
    try:
        for start, _end in periods(today, months):
            lines = fetch_month(client, cfg, start)
            runs.append(
                replace_billing_period(
                    db,
                    org_id=org_id,
                    source_kind="gcp_billing_export",
                    source_key=f"gcp:{conn.id}",
                    billing_period=start,
                    lines=lines,
                )
            )
    except GcpError as exc:
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
