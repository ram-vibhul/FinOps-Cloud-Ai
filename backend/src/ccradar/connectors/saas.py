"""Read-only cost connectors: Snowflake, Databricks, Datadog, MongoDB Atlas, OpenAI,
Anthropic, GitHub, Confluent Cloud, Vercel, Cloudflare, DigitalOcean, Linode, New Relic,
Fastly, Elastic Cloud and Grafana Cloud. More (Oracle Cloud, Alibaba Cloud, Hetzner, Twilio,
Sentry, Okta, Slack, Zoom, PagerDuty, Atlassian) are in ``extra.py``.

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
- **OpenAI**: ``/v1/organization/costs`` (daily cost per project and line item). Needs an
  admin key (organisation owners create them); ordinary API keys are refused.
- **Anthropic**: ``/v1/organizations/cost_report`` (daily cost per workspace and
  description, in cents as decimal strings). Needs an Admin API key.
- **GitHub**: ``/organizations/{org}/settings/billing/usage`` (the enhanced billing
  platform: daily usage per product, SKU and repository, with discounts). A
  fine-grained token with the organisation's "Administration: read" permission.
- **Confluent Cloud**: ``/billing/v1/costs`` (daily cost per product and resource;
  at most 31 days per request). A Cloud API key of an OrganizationAdmin or BillingAdmin.
- **Vercel**: ``/v1/billing/charges`` (FOCUS 1.3 rows as JSON lines, daily per product,
  with the project in the tags). A token of a team member with a billing-visible role.
- **Cloudflare**: ``/accounts/{id}/billable/usage`` (FOCUS-aligned daily rows, at most
  31 days per request). Cloudflare marks it alpha and limited to self-serve (PayGo)
  accounts for now. An API token with Billing Read.
- **DigitalOcean**: invoices (``/v2/customers/my/invoices``; the current month is the
  daily-updated invoice preview) and their items, per product and resource. A read-only
  token with the billing:read scope.
- **Linode (Akamai Cloud)**: invoices and their items (``/v4/account/invoices``), per
  service and region; tax as its own line. Linode bills in arrears, so a month appears
  once it is invoiced. A personal access token with Account: read only.
- **New Relic**: NerdGraph NRQL over ``NrConsumption`` (daily GB ingested per source,
  compute units) and ``NrMTDConsumption`` (billable users). New Relic gives usage, not
  money, so the cost is usage times the customer's own rates (per GB after the free
  allowance, per user after the free seats, per compute unit) and marked as an
  estimate. A User key (``NRAK-…``) and the account ID.
- **Fastly**: invoices (``/billing/v3/invoices``) and the month-to-date invoice, per
  product and region; monthly. An API token of a billing or superuser role.
- **Elastic Cloud**: itemized organisation costs (``/api/v2/billing/organizations/{id}/
  costs/items``) in Elastic Consumption Units, converted at 1 USD per ECU unless the
  customer enters their own rate; monthly. An API key of an organisation billing admin.
- **Grafana Cloud**: billed usage per dimension (``/api/orgs/{org}/billed-usage``),
  split by stack where Grafana attributes the cost. A Cloud Access Policy token that
  can read billing.
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
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from sqlalchemy.orm import Session

from ccradar.clouds.aws.cost_explorer import periods
from ccradar.costs.ingest import (
    CHARGE_CATEGORIES,
    PRICING_CATEGORIES,
    CostLine,
    replace_billing_period,
)
from ccradar.db.base import utcnow
from ccradar.db.tenant import set_tenant
from ccradar.models import CloudConnection, CostImportRun

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
OPENAI = "https://api.openai.com/v1/organization/costs"
ANTHROPIC = "https://api.anthropic.com/v1/organizations/cost_report"
GITHUB = "https://api.github.com"
CONFLUENT = "https://api.confluent.cloud/billing/v1/costs"
GITHUB_ORG = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
VERCEL = "https://api.vercel.com/v1/billing/charges"
VERCEL_TEAM = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
CLOUDFLARE = "https://api.cloudflare.com/client/v4/accounts"
CLOUDFLARE_ACCOUNT = re.compile(r"^[0-9a-f]{32}$")
DIGITALOCEAN = "https://api.digitalocean.com/v2/customers/my/invoices"
LINODE = "https://api.linode.com/v4/account/invoices"
FASTLY = "https://api.fastly.com/billing/v3/invoices"
ELASTIC = "https://api.elastic-cloud.com/api/v2/billing/organizations"
GRAFANA = "https://grafana.com/api/orgs"
SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$")
NEW_RELIC = {"US": "https://api.newrelic.com/graphql", "EU": "https://api.eu.newrelic.com/graphql"}
# New Relic list prices (USD) used when the customer enters none; please verify periodically.
NEW_RELIC_DEFAULTS = {
    "price_per_gb": "0.40",
    "free_gb": "100",
    "price_full_user": "349",
    "price_core_user": "49",
    "free_full_users": "1",
    "price_core_ccu": "0",
    "price_advanced_ccu": "0",
}


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


# Atlas SKU families → (service, FOCUS service category); first match wins.
ATLAS_SKUS = (
    ("DATA_TRANSFER", "Atlas data transfer", "Networking"),
    ("PRIVATE_ENDPOINT", "Atlas private endpoints", "Networking"),
    ("BACKUP", "Atlas backup", "Storage"),
    ("SNAPSHOT", "Atlas backup", "Storage"),
    ("PIT_RESTORE", "Atlas backup", "Storage"),
    ("STORAGE", "Atlas storage", "Storage"),
    ("SEARCH", "Atlas Search", "Databases"),
    ("DATA_LAKE", "Atlas Data Federation", "Analytics"),
    ("DATA_FEDERATION", "Atlas Data Federation", "Analytics"),
    ("STREAM", "Atlas Stream Processing", "Analytics"),
    ("SERVERLESS", "Atlas serverless", "Databases"),
    ("FLEX", "Atlas Flex clusters", "Databases"),
    ("APP_SERVICE", "Atlas App Services", "Developer Tools"),
    ("REALM", "Atlas App Services", "Developer Tools"),
    ("STITCH", "Atlas App Services", "Developer Tools"),
    ("SUPPORT", "Atlas support", "Management and Governance"),
    ("CHARTS", "Atlas Charts", "Analytics"),
    ("INSTANCE", "Atlas clusters", "Databases"),
)


def atlas_service(sku: str) -> tuple[str, str | None]:
    upper = sku.upper()
    for needle, service, category in ATLAS_SKUS:
        if needle in upper:
            return service, category
    return "MongoDB Atlas", None


def atlas_cloud(sku: str) -> str | None:
    """Atlas SKUs name the cloud provider (not the region)."""
    for cloud in ("AWS", "AZURE", "GCP"):
        if f"_{cloud}_" in f"_{sku.upper()}_":
            return cloud.lower()
    return None


def atlas_tags(li: dict[str, Any], sku: str) -> dict[str, str]:
    tags = (
        {str(k): str(v) for k, v in li["tags"].items()} if isinstance(li.get("tags"), dict) else {}
    )
    cloud = atlas_cloud(sku)
    if cloud:
        tags.setdefault("atlas_cloud", cloud)
    return tags


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
            sku = str(li.get("sku") or "")
            service, category = atlas_service(sku)
            discount = Decimal(int(li.get("discountCents") or 0)) / 100
            lines.append(
                CostLine(
                    charge_date=day,
                    provider="mongodb_atlas",
                    sub_account_id=li.get("groupId"),
                    sub_account_name=li.get("groupName"),
                    service_name=service,
                    service_category=category,
                    sku_id=sku or None,
                    usage_type=sku or None,
                    resource_id=li.get("clusterName") or li.get("stitchAppName"),
                    resource_name=li.get("clusterName") or li.get("stitchAppName"),
                    resource_type="Atlas cluster" if li.get("clusterName") else None,
                    list_cost=cost + discount if discount > 0 else None,
                    tags=atlas_tags(li, sku),
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


# --- OpenAI ------------------------------------------------------------------------


def _month_bounds(month: date) -> tuple[datetime, datetime]:
    start = datetime(month.year, month.month, 1, tzinfo=UTC)
    nxt = _next_month(month)
    return start, datetime(nxt.year, nxt.month, 1, tzinfo=UTC)


def openai_month(
    http: httpx.Client, cfg: dict[str, Any], secret: dict[str, Any], month: date
) -> list[CostLine]:
    start, end = _month_bounds(month)
    headers = {"Authorization": f"Bearer {secret['admin_key']}"}
    params: dict[str, Any] = {
        "start_time": int(start.timestamp()),
        "end_time": int(end.timestamp()),
        "bucket_width": "1d",
        "group_by": ["project_id", "line_item"],
        "limit": 31,
    }
    lines = []
    for _ in range(MAX_PAGES):
        page = _call(http, "GET", OPENAI, headers=headers, params=params).json()
        for bucket in page.get("data") or []:
            day = datetime.fromtimestamp(int(bucket["start_time"]), UTC).date()
            for item in bucket.get("results") or []:
                amount = item.get("amount") or {}
                cost = Decimal(str(amount.get("value") or 0))
                if cost == 0 or not month <= day < _next_month(month):
                    continue
                line_item = str(item.get("line_item") or "usage")
                lines.append(
                    CostLine(
                        charge_date=day,
                        provider="openai",
                        sub_account_id=item.get("project_id"),
                        service_name="OpenAI API",
                        usage_type=line_item[:300],
                        sku_id=line_item[:200],
                        billed_cost=cost,
                        effective_cost=cost,
                        billing_currency=str(amount.get("currency") or "usd").upper()[:3],
                        service_category="AI and Machine Learning",
                    )
                )
        if not page.get("has_more") or not page.get("next_page"):
            break
        params["page"] = page["next_page"]
    return lines


# --- Anthropic ---------------------------------------------------------------------


def anthropic_month(
    http: httpx.Client, cfg: dict[str, Any], secret: dict[str, Any], month: date
) -> list[CostLine]:
    start, end = _month_bounds(month)
    headers = {"x-api-key": secret["admin_key"], "anthropic-version": "2023-06-01"}
    params: dict[str, Any] = {
        "starting_at": start.isoformat().replace("+00:00", "Z"),
        "ending_at": end.isoformat().replace("+00:00", "Z"),
        "bucket_width": "1d",
        "group_by[]": ["workspace_id", "description"],
        "limit": 31,
    }
    lines = []
    for _ in range(MAX_PAGES):
        page = _call(http, "GET", ANTHROPIC, headers=headers, params=params).json()
        for bucket in page.get("data") or []:
            day = date.fromisoformat(str(bucket["starting_at"])[:10])
            for item in bucket.get("results") or []:
                cost = Decimal(str(item.get("amount") or 0)) / 100  # cents, as a decimal string
                if cost == 0 or not month <= day < _next_month(month):
                    continue
                description = str(item.get("description") or "Claude API usage")
                lines.append(
                    CostLine(
                        charge_date=day,
                        provider="anthropic",
                        sub_account_id=item.get("workspace_id") or "default",
                        service_name="Claude API",
                        usage_type=description[:300],
                        sku_id=(item.get("model") or None),
                        billed_cost=cost,
                        effective_cost=cost,
                        billing_currency=str(item.get("currency") or "USD").upper()[:3],
                        service_category="AI and Machine Learning",
                        extras={
                            k: item[k]
                            for k in ("cost_type", "token_type", "service_tier")
                            if item.get(k)
                        },
                    )
                )
        if not page.get("has_more") or not page.get("next_page"):
            break
        params["page"] = page["next_page"]
    return lines


# --- GitHub ------------------------------------------------------------------------


def github_month(
    http: httpx.Client, cfg: dict[str, Any], secret: dict[str, Any], month: date
) -> list[CostLine]:
    org = cfg["org"]
    if not GITHUB_ORG.match(org):
        raise ConnectorError("not a GitHub organisation name")
    headers = {
        "Authorization": f"Bearer {secret['token']}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    data = _call(
        http,
        "GET",
        f"{GITHUB}/organizations/{org}/settings/billing/usage",
        headers=headers,
        params={"year": month.year, "month": month.month},
    ).json()
    lines = []
    for item in data.get("usageItems") or []:
        day = date.fromisoformat(str(item.get("date", ""))[:10])
        gross = Decimal(str(item.get("grossAmount") or 0))
        net = Decimal(str(item.get("netAmount") if item.get("netAmount") is not None else gross))
        if (gross == 0 and net == 0) or not month <= day < _next_month(month):
            continue
        product = str(item.get("product") or "github")
        lines.append(
            CostLine(
                charge_date=day,
                provider="github",
                sub_account_id=item.get("organizationName") or org,
                service_name=f"GitHub {product.replace('_', ' ').title()}",
                sku_id=item.get("sku"),
                usage_type=item.get("sku"),
                resource_id=item.get("repositoryName") or None,
                billed_cost=net,
                effective_cost=net,
                list_cost=gross,
                billing_currency="USD",
                consumed_quantity=Decimal(str(item["quantity"]))
                if item.get("quantity") is not None
                else None,
                consumed_unit=item.get("unitType"),
                service_category="Developer Tools",
            )
        )
    return lines


# --- Confluent Cloud ---------------------------------------------------------------


def confluent_month(
    http: httpx.Client, cfg: dict[str, Any], secret: dict[str, Any], month: date
) -> list[CostLine]:
    auth = (secret["api_key"], secret["api_secret"])
    end = min(_next_month(month), utcnow().date())  # only finished days are reported
    if end <= month:
        return []
    params: dict[str, Any] = {"start_date": month.isoformat(), "end_date": end.isoformat()}
    lines = []
    for _ in range(MAX_PAGES):
        page = _call(http, "GET", CONFLUENT, auth=auth, params=params).json()
        for item in page.get("data") or []:
            day = date.fromisoformat(str(item.get("start_date", ""))[:10])
            cost = Decimal(str(item.get("amount") or 0))
            listed = Decimal(str(item.get("original_amount") or cost))
            if (cost == 0 and listed == 0) or not month <= day < _next_month(month):
                continue
            resource = item.get("resource") or {}
            env = (resource.get("environment") or {}).get("id")
            product = str(item.get("product") or "Confluent Cloud")
            lines.append(
                CostLine(
                    charge_date=day,
                    provider="confluent",
                    sub_account_id=env,
                    service_name=f"Confluent {product.replace('_', ' ').title()}",
                    usage_type=item.get("line_type"),
                    resource_id=resource.get("id"),
                    resource_name=resource.get("display_name"),
                    billed_cost=cost,
                    effective_cost=cost,
                    list_cost=listed,
                    billing_currency="USD",
                    consumed_quantity=Decimal(str(item["quantity"]))
                    if item.get("quantity") is not None
                    else None,
                    consumed_unit=item.get("unit"),
                )
            )
        token = (page.get("metadata") or {}).get("next")
        if not token:
            break
        params["page_token"] = (
            parse_qs(urlsplit(token).query).get("page_token", [token])[0] if "?" in token else token
        )
    return lines


# --- FOCUS rows (Vercel, Cloudflare) -----------------------------------------------


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _focus_line(row: dict[str, Any], provider: str, month: date) -> CostLine | None:
    """One FOCUS row as a cost line, or None when it is outside the month or zero."""
    day = date.fromisoformat(str(row.get("ChargePeriodStart") or "")[:10])
    billed = _dec(row.get("BilledCost"))
    if billed is None:
        billed = _dec(row.get("ContractedCost")) or Decimal(0)
    effective = _dec(row.get("EffectiveCost"))
    effective = billed if effective is None else effective
    listed = _dec(row.get("ListCost"))
    if (billed == 0 and effective == 0) or not month <= day < _next_month(month):
        return None
    category = str(row.get("ChargeCategory") or "Usage")
    pricing = row.get("PricingCategory")
    raw_tags = row.get("Tags")
    tags = {str(k): str(v) for k, v in raw_tags.items()} if isinstance(raw_tags, dict) else {}
    service = (
        row.get("ServiceName")
        or row.get("x_ProductFamilyName")
        or row.get("x_BillableMetricName")
        or provider.title()
    )
    sku = row.get("SkuId") or row.get("x_BillableMetricId")
    return CostLine(
        charge_date=day,
        provider=provider,
        service_name=str(service)[:200],
        charge_category=category if category in CHARGE_CATEGORIES else "Usage",
        pricing_category=pricing if pricing in PRICING_CATEGORIES else "Standard",
        billing_account_id=row.get("BillingAccountId"),
        sub_account_id=row.get("SubAccountId"),
        sub_account_name=row.get("SubAccountName"),
        service_category=row.get("ServiceCategory"),
        region=row.get("RegionId") or row.get("x_ZoneName"),
        resource_id=tags.get("ProjectId") or row.get("x_ZoneId") or row.get("ResourceId"),
        resource_name=tags.get("ProjectName") or row.get("x_ZoneName") or row.get("ResourceName"),
        sku_id=str(sku)[:200] if sku else None,
        usage_type=str(
            row.get("ChargeDescription") or row.get("x_BillableMetricName") or sku or ""
        )[:300]
        or None,
        consumed_quantity=_dec(row.get("ConsumedQuantity")),
        consumed_unit=row.get("ConsumedUnit"),
        billed_cost=billed,
        effective_cost=effective,
        list_cost=listed,
        billing_currency=str(row.get("BillingCurrency") or "USD").upper()[:3],
        tags=tags,
    )


def vercel_month(
    http: httpx.Client, cfg: dict[str, Any], secret: dict[str, Any], month: date
) -> list[CostLine]:
    team = cfg["team_id"]
    if not VERCEL_TEAM.match(team):
        raise ConnectorError("not a Vercel team ID or slug")
    start, end = _month_bounds(month)
    tomorrow = datetime.combine(utcnow().date() + timedelta(days=1), datetime.min.time(), UTC)
    end = min(end, tomorrow)
    if end <= start:
        return []
    key = "teamId" if team.startswith("team_") else "slug"
    r = _call(
        http,
        "GET",
        VERCEL,
        headers={"Authorization": f"Bearer {secret['token']}", "Accept-Encoding": "gzip"},
        params={
            key: team,
            "from": start.isoformat().replace("+00:00", "Z"),
            "to": end.isoformat().replace("+00:00", "Z"),
        },
    )
    lines = []
    for text in r.text.splitlines():
        if text.strip() and (line := _focus_line(json.loads(text), "vercel", month)):
            lines.append(line)
    return lines


def cloudflare_month(
    http: httpx.Client, cfg: dict[str, Any], secret: dict[str, Any], month: date
) -> list[CostLine]:
    account = cfg["account_id"]
    if not CLOUDFLARE_ACCOUNT.match(account):
        raise ConnectorError("not a Cloudflare account ID (32 hexadecimal characters)")
    last = min(_next_month(month) - timedelta(days=1), utcnow().date())
    if last < month:
        return []
    body = _call(
        http,
        "GET",
        f"{CLOUDFLARE}/{account}/billable/usage",
        headers={"Authorization": f"Bearer {secret['api_token']}"},
        params={"from": month.isoformat(), "to": last.isoformat()},  # at most 31 days
    ).json()
    if body.get("success") is False:
        raise ConnectorError(f"Cloudflare refused: {str(body.get('errors'))[:300]}")
    return [
        line for row in body.get("result") or [] if (line := _focus_line(row, "cloudflare", month))
    ]


# --- DigitalOcean ------------------------------------------------------------------


def _do_pages(http: httpx.Client, url: str, headers: dict[str, str], key: str) -> list[Any]:
    out: list[Any] = []
    params: dict[str, Any] | None = {"per_page": 200}
    for _ in range(MAX_PAGES):
        body = _call(http, "GET", url, headers=headers, params=params).json()
        out += body.get(key) or []
        nxt = ((body.get("links") or {}).get("pages") or {}).get("next")
        if not nxt or urlsplit(nxt).hostname != "api.digitalocean.com":
            break
        url, params = nxt, None  # the next link carries page and per_page
    return out


def digitalocean_month(
    http: httpx.Client, cfg: dict[str, Any], secret: dict[str, Any], month: date
) -> list[CostLine]:
    headers = {"Authorization": f"Bearer {secret['token']}"}
    period = f"{month:%Y-%m}"
    first = _call(http, "GET", DIGITALOCEAN, headers=headers, params={"per_page": 200}).json()
    preview = first.get("invoice_preview") or {}
    if preview.get("invoice_period") == period:
        uuid_ = preview.get("invoice_uuid")
    else:
        invoices = list(first.get("invoices") or [])
        if not any(i.get("invoice_period") == period for i in invoices):
            invoices = _do_pages(http, DIGITALOCEAN, headers, "invoices")
        uuid_ = next(
            (i.get("invoice_uuid") for i in invoices if i.get("invoice_period") == period), None
        )
    if not uuid_ or not re.match(r"^[0-9a-fA-F-]{36}$", str(uuid_)):
        return []
    lines = []
    for item in _do_pages(http, f"{DIGITALOCEAN}/{uuid_}", headers, "invoice_items"):
        cost = Decimal(str(item.get("amount") or 0))
        if cost == 0:
            continue
        start = str(item.get("start_time") or "")[:10]
        day = date.fromisoformat(start) if start else month
        day = min(max(day, month), _next_month(month) - timedelta(days=1))
        product = str(item.get("product") or "Other")
        lines.append(
            CostLine(
                charge_date=day,
                provider="digitalocean",
                service_name=f"DigitalOcean {product}"[:200],
                usage_type=(item.get("description") or None),
                resource_id=item.get("resource_uuid") or item.get("resource_id") or None,
                resource_name=item.get("group_description") or None,
                sub_account_name=item.get("project_name") or None,
                billed_cost=cost,
                effective_cost=cost,
                billing_currency="USD",
                consumed_quantity=_dec(item.get("duration")),
                consumed_unit=item.get("duration_unit"),
            )
        )
    return lines


# --- Linode (Akamai Cloud) ---------------------------------------------------------


def _linode_pages(http: httpx.Client, url: str, headers: dict[str, str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for page in range(1, MAX_PAGES + 1):
        body = _call(
            http, "GET", url, headers=headers, params={"page": page, "page_size": 500}
        ).json()
        out += body.get("data") or []
        if page >= int(body.get("pages") or 1):
            break
    return out


def _linode_service(label: str) -> tuple[str, str | None]:
    head, _, rest = label.partition(" - ")
    if head.startswith("Linode "):
        return "Linodes", label
    return head or "Linode", rest or None


def linode_month(
    http: httpx.Client, cfg: dict[str, Any], secret: dict[str, Any], month: date
) -> list[CostLine]:
    after = _next_month(_next_month(month))  # usage is invoiced at the start of next month
    filt = {
        "+and": [
            {"date": {"+gte": f"{month.isoformat()}T00:00:00"}},
            {"date": {"+lt": f"{after.isoformat()}T00:00:00"}},
        ]
    }
    headers = {"Authorization": f"Bearer {secret['token']}"}
    invoices = _linode_pages(http, LINODE, {**headers, "X-Filter": json.dumps(filt)})
    lines = []
    for inv in invoices:
        for item in _linode_pages(http, f"{LINODE}/{int(inv['id'])}/items", headers):
            day = date.fromisoformat(str(item.get("from") or "")[:10])
            if not month <= day < _next_month(month):
                continue
            label = str(item.get("label") or "Linode")
            service, name = _linode_service(label)
            common = {
                "charge_date": day,
                "provider": "linode",
                "billing_account_id": str(inv["id"]),
                "region": item.get("region"),
                "billing_currency": "USD",
            }
            amount = Decimal(str(item.get("amount") or 0))
            if amount:
                lines.append(
                    CostLine(
                        **common,
                        service_name=f"Akamai {service}"[:200],
                        resource_name=name,
                        usage_type=str(item.get("type") or "") or None,
                        billed_cost=amount,
                        effective_cost=amount,
                        consumed_quantity=_dec(item.get("quantity")),
                    )
                )
            tax = Decimal(str(item.get("tax") or 0))
            if tax:
                lines.append(
                    CostLine(
                        **common,
                        service_name=f"Akamai {service}"[:200],
                        resource_name=name,
                        charge_category="Tax",
                        pricing_category=None,
                        usage_type="Tax",
                        billed_cost=tax,
                        effective_cost=tax,
                    )
                )
    return lines


# --- New Relic ---------------------------------------------------------------------


def _nr_day(row: dict[str, Any]) -> date | None:
    begin = row.get("beginTimeSeconds")
    return datetime.fromtimestamp(int(begin), UTC).date() if begin is not None else None


def _nr_facet(row: dict[str, Any], name: str) -> str:
    facet = row.get(name) or row.get("facet") or "Other"
    return str(facet[0] if isinstance(facet, list) and facet else facet)


def newrelic_month(
    http: httpx.Client, cfg: dict[str, Any], secret: dict[str, Any], month: date
) -> list[CostLine]:
    account = str(cfg["account_id"])
    if not re.fullmatch(r"\d{1,12}", account):
        raise ConnectorError("not a New Relic account ID")
    url = NEW_RELIC.get(str(cfg.get("region") or "US").upper())
    if url is None:
        raise ConnectorError("region must be US or EU")
    try:
        rate = {k: Decimal(str(cfg.get(k) or v)) for k, v in NEW_RELIC_DEFAULTS.items()}
    except ArithmeticError as exc:  # decimal.InvalidOperation
        raise ConnectorError("rates must be numbers, for example 0.40") from exc
    if any(not v.is_finite() or v < 0 for v in rate.values()):
        raise ConnectorError("rates must be zero or more")
    start, end = _month_bounds(month)
    window = f"SINCE {int(start.timestamp() * 1000)} UNTIL {int(end.timestamp() * 1000)}"
    queries = {
        "ingest": "FROM NrConsumption SELECT sum(GigabytesIngested) "
        f"WHERE productLine = 'DataPlatform' FACET usageMetric {window} TIMESERIES 1 day "
        "LIMIT MAX",
        "compute": "FROM NrConsumption SELECT sum(consumption) "
        f"WHERE metric IN ('CoreCCU', 'AdvancedCCU') FACET metric {window} TIMESERIES 1 day",
        "users": "FROM NrMTDConsumption SELECT latest(FullPlatformUsersBillable), "
        "latest(FullUsersBillable), latest(CoreUsersBillable) " + window,
    }
    fields = " ".join(
        f"{name}: nrql(query: {json.dumps(q)}, timeout: 60) {{ results }}"
        for name, q in queries.items()
    )
    body = _call(
        http,
        "POST",
        url,
        headers={"API-Key": secret["user_key"], "Content-Type": "application/json"},
        json={"query": f"{{ actor {{ account(id: {account}) {{ {fields} }} }} }}"},
    ).json()
    if body.get("errors"):
        raise ConnectorError(f"New Relic refused: {str(body['errors'])[:300]}")
    found = ((body.get("data") or {}).get("actor") or {}).get("account") or {}

    def results(name: str) -> list[dict[str, Any]]:
        return list((found.get(name) or {}).get("results") or [])

    last_day = _next_month(month) - timedelta(days=1)
    estimate = {"estimate": True, "basis": "New Relic usage times your rates"}
    lines: list[CostLine] = []

    def add(day: date, service: str, usage: str, qty: Decimal, unit: str, cost: Decimal) -> None:
        if cost <= 0 or not month <= day <= last_day:
            return
        cost = cost.quantize(Decimal("0.0001"))
        lines.append(
            CostLine(
                charge_date=day,
                provider="newrelic",
                sub_account_id=account,
                service_name=service,
                usage_type=usage[:300],
                consumed_quantity=qty,
                consumed_unit=unit,
                billed_cost=cost,
                effective_cost=cost,
                billing_currency="USD",
                service_category="Management and Governance",
                extras=dict(estimate),
            )
        )

    free = rate["free_gb"]
    rows = sorted(
        (r for r in results("ingest") if _nr_day(r)),
        key=lambda r: (_nr_day(r), _nr_facet(r, "usageMetric")),
    )
    for r in rows:  # the free allowance is used up first, in day order
        gb = Decimal(str(r.get("sum.GigabytesIngested") or 0))
        covered = min(gb, free)
        free -= covered
        source = _nr_facet(r, "usageMetric")
        day = _nr_day(r) or month
        add(day, "New Relic Data Ingest", source, gb, "GB", (gb - covered) * rate["price_per_gb"])
    for r in results("compute"):
        metric = _nr_facet(r, "metric")
        price = rate["price_advanced_ccu" if metric == "AdvancedCCU" else "price_core_ccu"]
        ccu = Decimal(str(r.get("sum.consumption") or 0))
        add(_nr_day(r) or month, "New Relic Compute", metric, ccu, "CCU", ccu * price)
    users = (results("users") or [{}])[0]
    full = Decimal(
        str(
            users.get("latest.FullPlatformUsersBillable")
            or users.get("latest.FullUsersBillable")
            or 0
        )
    )
    core = Decimal(str(users.get("latest.CoreUsersBillable") or 0))
    billable_full = max(full - rate["free_full_users"], Decimal(0))
    add(month, "New Relic Users", "Full platform users", full, "users",
        billable_full * rate["price_full_user"])  # fmt: skip
    add(month, "New Relic Users", "Core users", core, "users", core * rate["price_core_user"])
    return lines


# --- Fastly ------------------------------------------------------------------------


def _fastly_lines(invoice: dict[str, Any], month: date) -> list[CostLine]:
    lines = []
    currency = str(invoice.get("currency_code") or "USD").upper()[:3]
    for item in invoice.get("transaction_line_items") or []:
        cost = Decimal(str(item.get("amount") or 0))
        if cost == 0:
            continue
        credit = bool(item.get("credit_coupon_code")) or cost < 0
        product = str(item.get("product_name") or item.get("product_line") or "Fastly")
        lines.append(
            CostLine(
                charge_date=month,
                provider="fastly",
                service_name=f"Fastly {product}"[:200],
                charge_category="Credit" if credit else "Usage",
                billing_account_id=invoice.get("customer_id"),
                region=item.get("region") or None,
                usage_type=(item.get("usage_type") or item.get("description") or None),
                sku_id=item.get("product_group") or None,
                consumed_quantity=_dec(item.get("units")),
                billed_cost=cost,
                effective_cost=cost,
                billing_currency=currency,
                extras={"invoice_id": invoice.get("invoice_id")}
                if invoice.get("invoice_id")
                else {},
            )
        )
    return lines


def fastly_month(
    http: httpx.Client, cfg: dict[str, Any], secret: dict[str, Any], month: date
) -> list[CostLine]:
    headers = {"Fastly-Key": secret["token"], "Accept": "application/json"}
    if month == utcnow().date().replace(day=1):
        mtd = _call(http, "GET", f"{FASTLY}/month-to-date", headers=headers).json()
        return _fastly_lines(mtd, month)
    params: dict[str, Any] = {
        "billing_start_date": month.isoformat(),
        "billing_end_date": (_next_month(month) - timedelta(days=1)).isoformat(),
        "limit": 200,
    }
    lines: list[CostLine] = []
    for _ in range(MAX_PAGES):
        body = _call(http, "GET", FASTLY, headers=headers, params=params).json()
        for inv in body.get("data") or []:
            if str(inv.get("billing_start_date") or "")[:7] == f"{month:%Y-%m}":
                lines += _fastly_lines(inv, month)
        cursor = (body.get("meta") or {}).get("next_cursor")
        if not cursor:
            break
        params["cursor"] = cursor
    return lines


# --- Elastic Cloud -----------------------------------------------------------------


def elastic_month(
    http: httpx.Client, cfg: dict[str, Any], secret: dict[str, Any], month: date
) -> list[CostLine]:
    org = str(cfg["organization_id"])
    if not SLUG.match(org):
        raise ConnectorError("not an Elastic Cloud organisation ID")
    try:
        per_ecu = Decimal(str(cfg.get("usd_per_ecu") or "1"))
    except ArithmeticError as exc:
        raise ConnectorError("the ECU rate must be a number, for example 1") from exc
    if not per_ecu.is_finite() or per_ecu < 0:
        raise ConnectorError("the ECU rate must be zero or more")
    start, end = _month_bounds(month)
    end = min(end, datetime.now(UTC).replace(minute=0, second=0, microsecond=0))
    if end <= start:
        return []
    body = _call(
        http,
        "GET",
        f"{ELASTIC}/{org}/costs/items",
        headers={"Authorization": f"ApiKey {secret['api_key']}", "Accept": "application/json"},
        params={"from": start.isoformat(), "to": end.isoformat()},
    ).json()
    lines = []
    for product in body.get("products") or []:
        kind = str(product.get("type") or "elastic")
        for item in product.get("product_line_items") or []:
            ecu = Decimal(str(item.get("total_ecu") or 0))
            if ecu == 0:
                continue
            cost = (ecu * per_ecu).quantize(Decimal("0.0001"))
            quantity = (item.get("quantity") or {}).get("value")
            lines.append(
                CostLine(
                    charge_date=month,
                    provider="elastic",
                    billing_account_id=org,
                    service_name=f"Elastic {kind.replace('_', ' ').title()}"[:200],
                    usage_type=str(item.get("name") or item.get("type") or "")[:300] or None,
                    sku_id=item.get("sku") or None,
                    consumed_quantity=_dec(quantity),
                    consumed_unit=item.get("unit") or None,
                    billed_cost=cost,
                    effective_cost=cost,
                    billing_currency="USD",
                    extras={"ecu": str(ecu), "usd_per_ecu": str(per_ecu)},
                )
            )
    return lines


# --- Grafana Cloud -----------------------------------------------------------------


def grafana_month(
    http: httpx.Client, cfg: dict[str, Any], secret: dict[str, Any], month: date
) -> list[CostLine]:
    org = str(cfg["org"])
    if not SLUG.match(org):
        raise ConnectorError("not a Grafana Cloud organisation slug")
    headers = {"Authorization": f"Bearer {secret['token']}", "Accept": "application/json"}
    lines: list[CostLine] = []
    cent = Decimal("0.01")
    for page in range(1, MAX_PAGES + 1):
        body = _call(
            http,
            "GET",
            f"{GRAFANA}/{org}/billed-usage",
            headers=headers,
            params={"month": month.month, "year": month.year, "page": page},
        ).json()
        for item in body.get("items") or []:
            due = Decimal(str(item.get("amountDue") or 0))
            if due == 0:
                continue
            service = f"Grafana {item.get('dimensionName') or item.get('dimensionId') or 'Cloud'}"
            common = {
                "charge_date": month,
                "provider": "grafana",
                "billing_account_id": org,
                "service_name": service[:200],
                "usage_type": item.get("description") or None,
                "sku_id": item.get("dimensionId") or None,
                "consumed_unit": item.get("unit") or None,
                "billing_currency": "USD",
            }
            stacks = [u for u in item.get("usages") or [] if u.get("attributedCost")]
            spread = Decimal(0)
            for u in stacks:
                cost = Decimal(str(u["attributedCost"]))
                spread += cost
                lines.append(
                    CostLine(
                        **common,
                        resource_id=str(u.get("stackId") or "") or None,
                        resource_name=u.get("stackName") or None,
                        consumed_quantity=_dec(u.get("totalUsage")),
                        billed_cost=cost,
                        effective_cost=cost,
                        tags={str(k): str(v) for k, v in (u.get("stackLabels") or {}).items()},
                    )
                )
            rest = due - spread
            if abs(rest) >= cent:  # not attributed to a stack (base fees, rounding)
                lines.append(
                    CostLine(
                        **common,
                        consumed_quantity=None if stacks else _dec(item.get("totalUsage")),
                        billed_cost=rest,
                        effective_cost=rest,
                    )
                )
        if page >= int(body.get("pages") or 1):
            break
    return lines


# --- registry and importer ---------------------------------------------------------


@dataclass(frozen=True)
class Connector:
    label: str
    settings: tuple[str, ...]  # required non-secret fields
    secrets: tuple[str, ...]  # required secret fields
    fetch: Callable[[httpx.Client, dict[str, Any], dict[str, Any], date], list[CostLine]]
    # alpha: an estimate (no billing API, or the vendor's API is itself in preview);
    # beta: real cost from the vendor's API, built to its documentation and tested on
    # recorded answers; stable: also checked on a live account.
    maturity: str = "beta"


CONNECTORS = {
    "snowflake": Connector("Snowflake", ("account", "user"), ("private_key",), snowflake_month),
    "databricks": Connector("Databricks", ("host", "warehouse_id"), ("token",), databricks_month),
    "datadog": Connector("Datadog", ("site",), ("api_key", "app_key"), datadog_month),
    "mongodb_atlas": Connector(
        "MongoDB Atlas", ("org_id",), ("public_key", "private_key"), atlas_month
    ),
    "openai": Connector("OpenAI", (), ("admin_key",), openai_month),
    "anthropic": Connector("Anthropic", (), ("admin_key",), anthropic_month),
    "github": Connector("GitHub", ("org",), ("token",), github_month),
    "confluent": Connector("Confluent Cloud", (), ("api_key", "api_secret"), confluent_month),
    "vercel": Connector("Vercel", ("team_id",), ("token",), vercel_month),
    "cloudflare": Connector(
        "Cloudflare", ("account_id",), ("api_token",), cloudflare_month, "alpha"
    ),
    "digitalocean": Connector("DigitalOcean", (), ("token",), digitalocean_month),
    "linode": Connector("Linode (Akamai Cloud)", (), ("token",), linode_month),
    "newrelic": Connector("New Relic", ("account_id",), ("user_key",), newrelic_month, "alpha"),
    "fastly": Connector("Fastly", (), ("token",), fastly_month),
    "elastic": Connector("Elastic Cloud", ("organization_id",), ("api_key",), elastic_month),
    "grafana": Connector("Grafana Cloud", ("org",), ("token",), grafana_month),
}
OPTIONAL = {
    "snowflake": ("warehouse", "role"),
    "newrelic": ("region", *NEW_RELIC_DEFAULTS),
    "elastic": ("usd_per_ecu",),
}

# More connectors register themselves into CONNECTORS and OPTIONAL (11.20).
from ccradar.connectors import extra as _extra  # noqa: E402


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
            try:
                lines = spec.fetch(http, cfg, secret, start)
            except _extra.SkipMonth:
                continue  # keep what was imported for that month before
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
