"""Read-only cost connectors: Snowflake, Databricks, Datadog and MongoDB Atlas.

Each connector turns one billing month into canonical cost lines; the shared importer
replaces that month for the connection, like the cloud imports. Credentials live in the
connection's encrypted ``secret`` (a JSON object) and are never returned. Every
connector talks only to its vendor's own hosts: fixed ones, or a customer-named host
that must match the vendor's domain pattern.

- **Snowflake**: SQL API with key-pair (JWT) auth; ``ORGANIZATION_USAGE.
  USAGE_IN_CURRENCY_DAILY`` (daily cost in the contract currency, per account and
  service type). Role needs ORGADMIN or a role granted access to that view.
- **Databricks**: SQL Statement Execution API on a SQL warehouse; ``system.billing.usage``
  priced with ``system.billing.list_prices`` (list price; negotiated discounts are not
  in system tables). Token needs SELECT on the billing schema.
- **Datadog**: ``/api/v2/usage/historical_cost`` (monthly cost per product; the current
  month comes from ``estimated_cost``). Needs an API key and an application key with
  the usage_read scope, on the parent organisation.
- **MongoDB Atlas**: invoices API (pending invoice plus the month's closed invoices);
  line items are daily per SKU and cluster. Programmatic API key with Organization
  Billing Viewer; digest auth.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from sqlalchemy.orm import Session

from costlens.clouds.aws.cost_explorer import periods
from costlens.costs.ingest import CostLine, replace_billing_period
from costlens.db.base import utcnow
from costlens.db.tenant import set_tenant
from costlens.models import CloudConnection, CostImportRun

TIMEOUT = 60.0
MAX_PAGES = 100
DATADOG_SITES = {
    "datadoghq.com",
    "us3.datadoghq.com",
    "us5.datadoghq.com",
    "datadoghq.eu",
    "ap1.datadoghq.com",
    "ap2.datadoghq.com",
    "ddog-gov.com",
}
SNOWFLAKE_ACCOUNT = re.compile(r"^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+){0,2}$")
DATABRICKS_HOST = re.compile(
    r"^[a-z0-9-]+(\.[a-z0-9-]+)*\.(cloud\.databricks\.com|azuredatabricks\.net|gcp\.databricks\.com)$"
)
ATLAS = "https://cloud.mongodb.com/api/atlas/v2"


class ConnectorError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def _next_month(d: date) -> date:
    return (d.replace(day=28) + timedelta(days=4)).replace(day=1)


def _call(http: httpx.Client, method: str, url: str, **kw: Any) -> httpx.Response:
    try:
        r = http.request(method, url, timeout=TIMEOUT, **kw)
    except httpx.HTTPError as exc:
        raise ConnectorError(f"could not reach the service ({type(exc).__name__})") from exc
    if r.status_code >= 400:
        raise ConnectorError(f"the service answered {r.status_code}: {r.text[:300]}", r.status_code)
    return r


# --- Snowflake ---------------------------------------------------------------------


def _snowflake_jwt(account: str, user: str, private_key_pem: str) -> str:
    key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    public = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    fp = "SHA256:" + base64.b64encode(hashlib.sha256(public).digest()).decode()
    acct = account.split(".", maxsplit=1)[0].upper()
    qualified = f"{acct}.{user.upper()}"
    now = int(time.time())
    return jwt.encode(
        {"iss": f"{qualified}.{fp}", "sub": qualified, "iat": now, "exp": now + 3540},
        private_key_pem,
        algorithm="RS256",
    )


def snowflake_month(
    http: httpx.Client, cfg: dict[str, Any], secret: dict[str, Any], month: date
) -> list[CostLine]:
    account = cfg["account"]
    if not SNOWFLAKE_ACCOUNT.match(account):
        raise ConnectorError("not a Snowflake account identifier")
    base = f"https://{account.lower()}.snowflakecomputing.com/api/v2/statements"
    headers = {
        "Authorization": f"Bearer {_snowflake_jwt(account, cfg['user'], secret['private_key'])}",
        "X-Snowflake-Authorization-Token-Type": "KEYPAIR_JWT",
        "Accept": "application/json",
    }
    body: dict[str, Any] = {
        "statement": (
            "SELECT usage_date, account_name, service_type, SUM(usage_in_currency), currency "
            "FROM snowflake.organization_usage.usage_in_currency_daily "
            "WHERE usage_date >= ? AND usage_date < ? "
            "GROUP BY usage_date, account_name, service_type, currency"
        ),
        "bindings": {
            "1": {"type": "TEXT", "value": month.isoformat()},
            "2": {"type": "TEXT", "value": _next_month(month).isoformat()},
        },
        "timeout": 120,
    }
    if cfg.get("warehouse"):
        body["warehouse"] = cfg["warehouse"]
    if cfg.get("role"):
        body["role"] = cfg["role"]
    r = _call(http, "POST", base, json=body, headers=headers)
    data = r.json()
    for _ in range(60):
        if r.status_code != 202:
            break
        time.sleep(0 if cfg.get("_fast") else 2)
        r = _call(http, "GET", f"{base}/{data['statementHandle']}", headers=headers)
        data = r.json()
    rows = list(data.get("data") or [])
    parts = (data.get("resultSetMetaData") or {}).get("partitionInfo") or []
    for i in range(1, len(parts)):
        page = _call(
            http,
            "GET",
            f"{base}/{data['statementHandle']}",
            params={"partition": i},
            headers=headers,
        )
        rows += page.json().get("data") or []
    lines = []
    for day, acct, service, cost, currency in rows:
        amount = Decimal(str(cost or 0))
        if amount == 0:
            continue
        lines.append(
            CostLine(
                charge_date=date.fromisoformat(str(day)[:10]),
                provider="snowflake",
                sub_account_id=acct,
                sub_account_name=acct,
                service_name=f"Snowflake {str(service).replace('_', ' ').title()}",
                billed_cost=amount,
                effective_cost=amount,
                billing_currency=str(currency or "USD").upper()[:3],
                charge_category="Credit" if amount < 0 else "Usage",
                pricing_category=None if amount < 0 else "Standard",
            )
        )
    return lines


# --- Databricks --------------------------------------------------------------------

DATABRICKS_SQL = """
SELECT u.usage_date, u.workspace_id, u.sku_name, u.billing_origin_product,
       coalesce(u.usage_metadata.cluster_id, u.usage_metadata.warehouse_id, u.usage_metadata.job_id) AS resource,
       to_json(u.custom_tags) AS tags, SUM(u.usage_quantity) AS quantity, MIN(u.usage_unit) AS unit,
       SUM(u.usage_quantity * lp.pricing.default) AS cost, MIN(lp.currency_code) AS currency
FROM system.billing.usage u
JOIN system.billing.list_prices lp
  ON u.cloud = lp.cloud AND u.sku_name = lp.sku_name AND u.usage_unit = lp.usage_unit
 AND u.usage_end_time >= lp.price_start_time
 AND (lp.price_end_time IS NULL OR u.usage_end_time < lp.price_end_time)
WHERE u.usage_date >= :start AND u.usage_date < :end
GROUP BY u.usage_date, u.workspace_id, u.sku_name, u.billing_origin_product, resource, tags
"""


def databricks_month(
    http: httpx.Client, cfg: dict[str, Any], secret: dict[str, Any], month: date
) -> list[CostLine]:
    host = cfg["host"]
    if not DATABRICKS_HOST.match(host):
        raise ConnectorError("not a Databricks workspace address")
    base = f"https://{host}/api/2.0/sql/statements"
    headers = {"Authorization": f"Bearer {secret['token']}"}
    r = _call(
        http,
        "POST",
        base,
        headers=headers,
        json={
            "warehouse_id": cfg["warehouse_id"],
            "statement": DATABRICKS_SQL,
            "parameters": [
                {"name": "start", "value": month.isoformat(), "type": "DATE"},
                {"name": "end", "value": _next_month(month).isoformat(), "type": "DATE"},
            ],
            "wait_timeout": "30s",
            "on_wait_timeout": "CONTINUE",
            "format": "JSON_ARRAY",
            "disposition": "INLINE",
        },
    ).json()
    for _ in range(60):
        state = (r.get("status") or {}).get("state")
        if state in ("SUCCEEDED", "FAILED", "CANCELED", "CLOSED"):
            break
        time.sleep(0 if cfg.get("_fast") else 2)
        r = _call(http, "GET", f"{base}/{r['statement_id']}", headers=headers).json()
    if (r.get("status") or {}).get("state") != "SUCCEEDED":
        err = (r.get("status") or {}).get("error") or {}
        raise ConnectorError(f"query failed: {err.get('message', 'unknown error')[:300]}")
    cols = [c["name"] for c in (r.get("manifest") or {}).get("schema", {}).get("columns", [])]
    result = r.get("result") or {}
    rows = list(result.get("data_array") or [])
    chunk = result.get("next_chunk_index")
    for _ in range(MAX_PAGES):
        if chunk is None:
            break
        page = _call(
            http, "GET", f"{base}/{r['statement_id']}/result/chunks/{chunk}", headers=headers
        ).json()
        rows += page.get("data_array") or []
        chunk = page.get("next_chunk_index")
    lines = []
    for raw in rows:
        x = dict(zip(cols, raw, strict=False))
        cost = Decimal(str(x.get("cost") or 0))
        if cost == 0:
            continue
        try:
            tags = {str(k): str(v) for k, v in (json.loads(x.get("tags") or "{}") or {}).items()}
        except ValueError:
            tags = {}
        lines.append(
            CostLine(
                charge_date=date.fromisoformat(str(x["usage_date"])[:10]),
                provider="databricks",
                sub_account_id=str(x.get("workspace_id") or "") or None,
                service_name=f"Databricks {str(x.get('billing_origin_product') or 'usage').replace('_', ' ').title()}",
                sku_id=x.get("sku_name"),
                usage_type=x.get("sku_name"),
                resource_id=x.get("resource"),
                billed_cost=cost,
                effective_cost=cost,
                list_cost=cost,
                billing_currency=str(x.get("currency") or "USD").upper()[:3],
                consumed_quantity=Decimal(str(x["quantity"]))
                if x.get("quantity") is not None
                else None,
                consumed_unit=x.get("unit"),
                tags=tags,
            )
        )
    return lines


# --- Datadog -----------------------------------------------------------------------


def datadog_month(
    http: httpx.Client, cfg: dict[str, Any], secret: dict[str, Any], month: date
) -> list[CostLine]:
    site = cfg.get("site") or "datadoghq.com"
    if site not in DATADOG_SITES:
        raise ConnectorError("unknown Datadog site")
    headers = {
        "DD-API-KEY": secret["api_key"],
        "DD-APPLICATION-KEY": secret["app_key"],
        "Accept": "application/json",
    }
    current = month == utcnow().date().replace(day=1)
    path = "estimated_cost" if current else "historical_cost"
    r = _call(
        http,
        "GET",
        f"https://api.{site}/api/v2/usage/{path}",
        headers=headers,
        params={
            "start_month": f"{month.isoformat()}T00:00:00Z",
            "end_month": f"{month.isoformat()}T00:00:00Z",
            "view": "sub-org",
        },
    ).json()
    lines = []
    for item in r.get("data") or []:
        a = item.get("attributes") or {}
        org = a.get("org_name") or a.get("public_id")
        for ch in a.get("charges") or []:
            if ch.get("charge_type") != "total":
                continue
            cost = Decimal(str(ch.get("cost") or 0))
            if cost == 0:
                continue
            lines.append(
                CostLine(
                    charge_date=month,  # Datadog reports per month; dated on the 1st
                    provider="datadog",
                    sub_account_id=a.get("public_id"),
                    sub_account_name=org,
                    service_name=f"Datadog {str(ch.get('product_name', 'usage')).replace('_', ' ').title()}",
                    billed_cost=cost,
                    effective_cost=cost,
                    billing_currency=str(a.get("currency") or "USD").upper()[:3],
                    extras={"estimated": current, "monthly": True},
                )
            )
    return lines


# --- MongoDB Atlas -----------------------------------------------------------------


def atlas_month(
    http: httpx.Client, cfg: dict[str, Any], secret: dict[str, Any], month: date
) -> list[CostLine]:
    org = cfg["org_id"]
    if not re.fullmatch(r"[0-9a-f]{24}", org):
        raise ConnectorError("not an Atlas organisation ID")
    auth = httpx.DigestAuth(secret["public_key"], secret["private_key"])
    headers = {"Accept": "application/vnd.atlas.2023-01-01+json"}
    end = _next_month(month)
    invoices = _call(
        http,
        "GET",
        f"{ATLAS}/orgs/{org}/invoices",
        auth=auth,
        headers=headers,
        params={"itemsPerPage": 100},
    ).json()
    ids = [
        i["id"]
        for i in invoices.get("results") or []
        if str(i.get("startDate", ""))[:7]
        in (month.strftime("%Y-%m"), (month - timedelta(days=1)).strftime("%Y-%m"))
    ]
    docs = [
        _call(http, "GET", f"{ATLAS}/orgs/{org}/invoices/{i}", auth=auth, headers=headers).json()
        for i in ids
    ]
    try:
        docs.append(
            _call(
                http, "GET", f"{ATLAS}/orgs/{org}/invoices/pending", auth=auth, headers=headers
            ).json()
        )
    except ConnectorError as exc:
        if exc.status != 404:
            raise
    seen: set[tuple[str, str, str, str]] = set()
    lines = []
    for doc in docs:
        for li in doc.get("lineItems") or []:
            day = (
                date.fromisoformat(str(li.get("startDate", ""))[:10])
                if li.get("startDate")
                else None
            )
            if day is None or not month <= day < end:
                continue
            key = (
                str(day),
                li.get("sku", ""),
                li.get("clusterName") or "",
                li.get("groupId") or "",
            )
            if key in seen:  # an item can appear on both a closed and the pending invoice
                continue
            seen.add(key)
            cost = Decimal(int(li.get("totalPriceCents") or 0)) / 100
            if cost == 0:
                continue
            lines.append(
                CostLine(
                    charge_date=day,
                    provider="mongodb_atlas",
                    sub_account_id=li.get("groupId"),
                    sub_account_name=li.get("groupName"),
                    service_name="MongoDB Atlas",
                    sku_id=li.get("sku"),
                    usage_type=li.get("sku"),
                    resource_id=li.get("clusterName"),
                    billed_cost=cost,
                    effective_cost=cost,
                    billing_currency="USD",
                    charge_category="Credit" if cost < 0 else "Usage",
                    pricing_category=None if cost < 0 else "Standard",
                    consumed_quantity=Decimal(str(li["quantity"]))
                    if li.get("quantity") is not None
                    else None,
                    consumed_unit=li.get("unit"),
                )
            )
    return lines


# --- registry and importer ---------------------------------------------------------


@dataclass(frozen=True)
class Connector:
    label: str
    settings: tuple[str, ...]  # required non-secret fields
    secrets: tuple[str, ...]  # required secret fields
    fetch: Callable[[httpx.Client, dict[str, Any], dict[str, Any], date], list[CostLine]]


CONNECTORS = {
    "snowflake": Connector("Snowflake", ("account", "user"), ("private_key",), snowflake_month),
    "databricks": Connector("Databricks", ("host", "warehouse_id"), ("token",), databricks_month),
    "datadog": Connector("Datadog", ("site",), ("api_key", "app_key"), datadog_month),
    "mongodb_atlas": Connector(
        "MongoDB Atlas", ("org_id",), ("public_key", "private_key"), atlas_month
    ),
}
OPTIONAL = {"snowflake": ("warehouse", "role")}


def import_connection(
    db: Session,
    http: httpx.Client,
    connection_id: uuid.UUID,
    *,
    months: int = 2,
    today: date | None = None,
) -> list[CostImportRun]:
    set_tenant(db, bypass=True)
    conn = db.get(CloudConnection, connection_id)
    if conn is None or conn.provider not in CONNECTORS or conn.status != "active":
        return []
    spec = CONNECTORS[conn.provider]
    cfg, secret = conn.settings or {}, json.loads(conn.secret or "{}")
    org_id, today = conn.org_id, today or utcnow().date()
    runs = []
    try:
        for start, _ in periods(today, months):
            lines = spec.fetch(http, cfg, secret, start)
            runs.append(
                replace_billing_period(
                    db,
                    org_id=org_id,
                    source_kind=conn.provider,
                    source_key=f"{conn.provider}:{conn.id}",
                    billing_period=start,
                    lines=lines,
                )
            )
    except (ConnectorError, KeyError, ValueError) as exc:
        set_tenant(db, bypass=True)
        conn.last_error = str(exc)[:1000]
        if isinstance(exc, ConnectorError) and exc.status in (401, 403):
            conn.status = "error"
        db.flush()
        return runs
    set_tenant(db, bypass=True)
    conn.last_checked_at, conn.last_error = utcnow(), None
    db.flush()
    return runs
