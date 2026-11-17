"""More cost connectors (11.20): Oracle Cloud, Alibaba Cloud, Hetzner Cloud, Twilio,
Sentry, and seat-priced tools (Okta, Slack, Zoom, PagerDuty, Atlassian Jira).

Each one is read-only and talks only to its vendor's own hosts. Maturity, shown to
customers next to each connector:

- **beta**: the vendor's billing or usage API gives money; built to the vendor's
  documentation and tested against recorded answers, not yet checked on a live account.
- **alpha**: the vendor has no billing API, so the cost is an *estimate*: usage or seats
  read from the API, times prices the customer enters (marked ``extras.estimate``).

Notes per vendor:

- **Oracle Cloud (OCI)**: Usage API ``POST /20200107/usage`` (daily cost per service,
  region, compartment and SKU) in the home region, signed with the API signing key
  (HTTP Signatures, RSA-SHA256). A user in a group allowed ``read usage-reports``.
- **Alibaba Cloud**: BSS OpenAPI ``QueryAccountBill`` (2017-12-14), daily and grouped by
  product, signed with an AccessKey (HMAC-SHA1). A RAM user with
  AliyunBSSReadOnlyAccess. The current month is provisional until the 3rd of the next.
- **Hetzner Cloud**: no billing API. Servers, volumes, load balancers and primary IPs of
  the project, priced from Hetzner's own price list (net, before VAT) for the hours they
  existed this month. Resources deleted before the import are not seen.
- **Twilio**: daily usage records with their price; the per-category lines are the
  most specific categories, and any difference to Twilio's own total is one "Other"
  line, so the total always matches. An API key (SK…) and secret.
- **Sentry**: accepted events per category from ``stats_v2``, times the on-demand rates
  the customer enters, plus their monthly plan fee.
- **Okta, Slack, Zoom, PagerDuty, Atlassian (Jira)**: active billable seats times the
  customer's price per seat. Seat counts are read now, so only the current month is
  estimated; earlier months keep what was imported then.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import re
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from email.utils import formatdate
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from ccradar.connectors.saas import (
    CONNECTORS,
    MAX_PAGES,
    OPTIONAL,
    Connector,
    ConnectorError,
    _call,
    _dec,
    _month_bounds,
    _next_month,
)
from ccradar.costs.ingest import CostLine
from ccradar.db.base import utcnow


class SkipMonth(ConnectorError):
    """Nothing can be known about this month now (seat counts of a past month): keep
    what was imported before instead of replacing it."""


def _rate(cfg: dict[str, Any], key: str, default: str | None = None) -> Decimal:
    raw = cfg.get(key) or default
    if raw is None:
        raise ConnectorError(f"enter {key.replace('_', ' ')}")
    try:
        value = Decimal(str(raw))
    except InvalidOperation as exc:
        raise ConnectorError(f"{key.replace('_', ' ')} must be a number") from exc
    if not value.is_finite() or value < 0:
        raise ConnectorError(f"{key.replace('_', ' ')} must be zero or more")
    return value


def _rates(raw: str | None) -> dict[str, Decimal]:
    """``"error=0.00029, transaction=0.000025"`` → {category: price}."""
    out: dict[str, Decimal] = {}
    for part in (raw or "").split(","):
        if not part.strip():
            continue
        key, _, value = part.partition("=")
        try:
            price = Decimal(value.strip())
        except InvalidOperation as exc:
            raise ConnectorError(f"not a price: {part.strip()!r}") from exc
        if not price.is_finite() or price < 0:
            raise ConnectorError(f"not a price: {part.strip()!r}")
        out[key.strip().lower()] = price
    return out


def _current_month_only(month: date) -> None:
    if month != utcnow().date().replace(day=1):
        raise SkipMonth("seat counts are only known for the current month")


def _seat_line(
    provider: str,
    service: str,
    seats: int,
    price: Decimal,
    month: date,
    account: str | None,
    usage_type: str | None = None,
) -> CostLine:
    cost = (price * seats).quantize(Decimal("0.0001"))
    return CostLine(
        charge_date=month,
        provider=provider,
        billing_account_id=account,
        service_name=service,
        usage_type=usage_type,
        consumed_quantity=Decimal(seats),
        consumed_unit="Seats",
        billed_cost=cost,
        effective_cost=cost,
        billing_currency="USD",
        extras={"estimate": True, "seats": seats, "price_per_seat": str(price)},
    )


# --- Oracle Cloud (OCI) --------------------------------------------------------------

OCI_REGION = re.compile(r"^[a-z]{2,4}-[a-z]+-\d$")
OCID = re.compile(r"^ocid1\.[a-z]+\.[a-z0-9]+\.[a-z0-9-]*\.[a-z0-9]{20,}$")
FINGERPRINT = re.compile(r"^([0-9a-f]{2}:){15}[0-9a-f]{2}$")


def oci_sign(
    method: str, url: str, headers: dict[str, str], body: bytes, key_id: str, private_key_pem: str
) -> dict[str, str]:
    """OCI request signing (HTTP Signatures, RSA-SHA256). Returns the headers to send."""
    parts = urlsplit(url)
    target = parts.path + (f"?{parts.query}" if parts.query else "")
    signed = {
        "date": formatdate(usegmt=True),
        "host": parts.netloc,
        "content-type": "application/json",
        "content-length": str(len(body)),
        "x-content-sha256": base64.b64encode(hashlib.sha256(body).digest()).decode(),
        **{k.lower(): v for k, v in headers.items()},
    }
    order = [
        "date",
        "(request-target)",
        "host",
        "content-length",
        "content-type",
        "x-content-sha256",
    ]
    text = "\n".join(
        f"(request-target): {method.lower()} {target}"
        if h == "(request-target)"
        else f"{h}: {signed[h]}"
        for h in order
    )
    try:
        key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    except (ValueError, TypeError) as exc:
        raise ConnectorError("the private key is not an unencrypted PEM key") from exc
    if not isinstance(key, rsa.RSAPrivateKey):
        raise ConnectorError("the API signing key must be an RSA key")
    signature = base64.b64encode(key.sign(text.encode(), padding.PKCS1v15(), hashes.SHA256()))
    signed["authorization"] = (
        f'Signature version="1",keyId="{key_id}",algorithm="rsa-sha256",'
        f'headers="{" ".join(order)}",signature="{signature.decode()}"'
    )
    return signed


def oracle_month(
    http: httpx.Client, cfg: dict[str, Any], secret: dict[str, Any], month: date
) -> list[CostLine]:
    tenancy, user, region = cfg["tenancy_ocid"], cfg["user_ocid"], cfg["region"]
    fingerprint = str(cfg["fingerprint"]).lower()
    if not (OCID.match(tenancy) and OCID.match(user)):
        raise ConnectorError("the tenancy and user are OCIDs (ocid1.…)")
    if not OCI_REGION.match(region):
        raise ConnectorError("the home region is like us-ashburn-1")
    if not FINGERPRINT.match(fingerprint):
        raise ConnectorError("the fingerprint is like 12:34:…:ef")
    start, end = _month_bounds(month)
    tomorrow = datetime.combine(utcnow().date() + timedelta(days=1), datetime.min.time(), UTC)
    end = min(end, tomorrow)
    if end <= start:
        return []
    url = f"https://usageapi.{region}.oci.oraclecloud.com/20200107/usage"
    lines: list[CostLine] = []
    page: str | None = None
    for _ in range(MAX_PAGES):
        body = json.dumps(
            {
                "tenantId": tenancy,
                "timeUsageStarted": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "timeUsageEnded": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "granularity": "DAILY",
                "queryType": "COST",
                "groupBy": ["service", "region", "compartmentName", "skuName"],
                "compartmentDepth": 1,
            },
            separators=(",", ":"),
        ).encode()
        target = f"{url}?page={quote(page)}" if page else url
        headers = oci_sign(
            "POST", target, {}, body, f"{tenancy}/{user}/{fingerprint}", secret["private_key"]
        )
        r = _call(http, "POST", target, content=body, headers=headers)
        for item in r.json().get("items") or []:
            cost = _dec(item.get("computedAmount"))
            if not cost:
                continue
            day = date.fromisoformat(str(item.get("timeUsageStarted", ""))[:10])
            lines.append(
                CostLine(
                    charge_date=day,
                    provider="oracle",
                    billing_account_id=tenancy,
                    sub_account_name=item.get("compartmentName") or None,
                    service_name=str(item.get("service") or "Oracle Cloud")[:200],
                    region=item.get("region") or None,
                    usage_type=item.get("skuName") or None,
                    sku_id=item.get("skuPartNumber") or None,
                    consumed_quantity=_dec(item.get("computedQuantity")),
                    consumed_unit=item.get("unit") or None,
                    billed_cost=cost,
                    effective_cost=cost,
                    billing_currency=str(item.get("currency") or "USD").upper()[:3],
                    charge_category="Credit" if cost < 0 else "Usage",
                    pricing_category=None if cost < 0 else "Standard",
                )
            )
        page = r.headers.get("opc-next-page")
        if not page:
            break
    return lines


# --- Alibaba Cloud ------------------------------------------------------------------

ALIBABA = {
    "international": "https://business.ap-southeast-1.aliyuncs.com/",
    "china": "https://business.aliyuncs.com/",
}


def _pct(value: str) -> str:
    return quote(value, safe="~")


def alibaba_sign(params: dict[str, str], access_secret: str) -> str:
    """RPC signature v1: HMAC-SHA1 over the sorted, percent-encoded query."""
    canonical = "&".join(f"{_pct(k)}={_pct(v)}" for k, v in sorted(params.items()))
    text = f"GET&{_pct('/')}&{_pct(canonical)}"
    mac = hmac.new(f"{access_secret}&".encode(), text.encode(), hashlib.sha1)
    return base64.b64encode(mac.digest()).decode()


def alibaba_month(
    http: httpx.Client, cfg: dict[str, Any], secret: dict[str, Any], month: date
) -> list[CostLine]:
    site = cfg.get("site") or "international"
    if site not in ALIBABA:
        raise ConnectorError("the site is international or china")
    today = utcnow().date()
    last = min(_next_month(month) - timedelta(days=1), today)
    lines: list[CostLine] = []
    day = month
    while day <= last:
        for page in range(1, MAX_PAGES + 1):
            params = {
                "Action": "QueryAccountBill",
                "Version": "2017-12-14",
                "Format": "JSON",
                "AccessKeyId": secret["access_key_id"],
                "SignatureMethod": "HMAC-SHA1",
                "SignatureVersion": "1.0",
                "SignatureNonce": uuid.uuid4().hex,
                "Timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "BillingCycle": month.strftime("%Y-%m"),
                "Granularity": "DAILY",
                "BillingDate": day.isoformat(),
                "IsGroupByProduct": "true",
                "PageNum": str(page),
                "PageSize": "300",
            }
            params["Signature"] = alibaba_sign(params, secret["access_key_secret"])
            body = _call(http, "GET", ALIBABA[site], params=params).json()
            if not body.get("Success", False):
                raise ConnectorError(
                    f"Alibaba Cloud answered {body.get('Code')}: {body.get('Message', '')[:200]}"
                )
            data = body.get("Data") or {}
            items = (data.get("Items") or {}).get("Item") or []
            for item in items:
                cost = _dec(item.get("PretaxAmount"))
                if not cost:
                    continue
                lines.append(
                    CostLine(
                        charge_date=date.fromisoformat(str(item.get("BillingDate") or day)[:10]),
                        provider="alibaba",
                        billing_account_id=str(item.get("OwnerID") or "") or None,
                        sub_account_id=str(item.get("OwnerID") or "") or None,
                        sub_account_name=item.get("OwnerName") or None,
                        service_name=str(
                            item.get("ProductName") or item.get("ProductCode") or "Alibaba Cloud"
                        )[:200],
                        sku_id=item.get("ProductCode") or None,
                        usage_type=item.get("SubscriptionType") or None,
                        billed_cost=cost,
                        effective_cost=cost,
                        billing_currency=str(item.get("Currency") or "USD").upper()[:3],
                        charge_category="Credit" if cost < 0 else "Usage",
                        pricing_category=None if cost < 0 else "Standard",
                        tags={"cost_unit": item["CostUnit"]} if item.get("CostUnit") else {},
                    )
                )
            total = int(data.get("TotalCount") or 0)
            if page * int(data.get("PageSize") or 300) >= total:
                break
        day += timedelta(days=1)
    return lines


# --- Hetzner Cloud (estimate) -------------------------------------------------------

HETZNER = "https://api.hetzner.cloud/v1"


def _hetzner_pages(http: httpx.Client, path: str, key: str, token: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    page: int | None = 1
    while page and len(out) < 50 * MAX_PAGES:
        body = _call(
            http,
            "GET",
            f"{HETZNER}/{path}",
            headers={"Authorization": f"Bearer {token}"},
            params={"page": page, "per_page": 50},
        ).json()
        out += body.get(key) or []
        page = ((body.get("meta") or {}).get("pagination") or {}).get("next_page")
    return out


def _price_at(prices: list[dict[str, Any]], location: str) -> tuple[Decimal, Decimal] | None:
    for p in prices or []:
        if p.get("location") == location:
            hourly = _dec((p.get("price_hourly") or {}).get("net"))
            monthly = _dec((p.get("price_monthly") or {}).get("net"))
            if hourly is not None and monthly is not None:
                return hourly, monthly
    return None


def _hours(created: str | None, start: datetime, end: datetime) -> Decimal:
    since = start
    if created:
        with contextlib.suppress(ValueError):
            since = max(start, datetime.fromisoformat(created.replace("Z", "+00:00")))
    return Decimal(max(0, int((end - since).total_seconds() // 3600)))


def hetzner_month(
    http: httpx.Client, cfg: dict[str, Any], secret: dict[str, Any], month: date
) -> list[CostLine]:
    _current_month_only(month)  # only what exists now is visible
    token = secret["token"]
    start, end = _month_bounds(month)
    end = min(end, datetime.now(UTC))
    pricing = (
        _call(http, "GET", f"{HETZNER}/pricing", headers={"Authorization": f"Bearer {token}"})
        .json()
        .get("pricing")
        or {}
    )
    currency = str(pricing.get("currency") or "EUR").upper()[:3]
    project = cfg.get("project") or None
    lines: list[CostLine] = []

    def add(
        service: str, rid: str, name: str, location: str, kind: str, cost: Decimal, hours: Decimal
    ) -> None:
        cost = cost.quantize(Decimal("0.0001"))
        if cost <= 0:
            return
        lines.append(
            CostLine(
                charge_date=month,
                provider="hetzner",
                billing_account_id=project,
                service_name=service,
                region=location or None,
                resource_id=rid,
                resource_name=name or None,
                resource_type=kind,
                consumed_quantity=hours,
                consumed_unit="Hours",
                billed_cost=cost,
                effective_cost=cost,
                billing_currency=currency,
                extras={"estimate": True, "basis": "Hetzner price list, net"},
            )
        )

    def priced(prices: list[dict[str, Any]], location: str, hours: Decimal) -> Decimal:
        found = _price_at(prices, location)
        if found is None:
            return Decimal(0)
        hourly, monthly = found
        return min(hourly * hours, monthly)

    for s in _hetzner_pages(http, "servers", "servers", token):
        location = ((s.get("datacenter") or {}).get("location") or {}).get("name") or ""
        hours = _hours(s.get("created"), start, end)
        cost = priced((s.get("server_type") or {}).get("prices") or [], location, hours)
        add(
            "Cloud Servers",
            f"server/{s.get('id')}",
            s.get("name") or "",
            location,
            str((s.get("server_type") or {}).get("name") or "server"),
            cost,
            hours,
        )
    per_gb = _dec(((pricing.get("volume") or {}).get("price_per_gb_month") or {}).get("net"))
    for v in _hetzner_pages(http, "volumes", "volumes", token):
        location = (v.get("location") or {}).get("name") or ""
        hours = _hours(v.get("created"), start, end)
        month_hours = Decimal((_month_bounds(month)[1] - _month_bounds(month)[0]).days * 24)
        cost = (
            (per_gb or 0) * Decimal(int(v.get("size") or 0)) * min(hours / month_hours, Decimal(1))
        )
        add(
            "Volumes", f"volume/{v.get('id')}", v.get("name") or "", location, "volume", cost, hours
        )
    for lb in _hetzner_pages(http, "load_balancers", "load_balancers", token):
        location = (lb.get("location") or {}).get("name") or ""
        hours = _hours(lb.get("created"), start, end)
        cost = priced((lb.get("load_balancer_type") or {}).get("prices") or [], location, hours)
        add(
            "Load Balancers",
            f"load_balancer/{lb.get('id')}",
            lb.get("name") or "",
            location,
            str((lb.get("load_balancer_type") or {}).get("name") or "lb"),
            cost,
            hours,
        )
    ip_prices = {p.get("type"): p.get("prices") or [] for p in pricing.get("primary_ips") or []}
    for ip in _hetzner_pages(http, "primary_ips", "primary_ips", token):
        location = ((ip.get("datacenter") or {}).get("location") or {}).get("name") or ""
        hours = _hours(ip.get("created"), start, end)
        cost = priced(ip_prices.get(ip.get("type")) or [], location, hours)
        add(
            "Primary IPs",
            f"primary_ip/{ip.get('id')}",
            ip.get("name") or "",
            location,
            str(ip.get("type") or "ip"),
            cost,
            hours,
        )
    return lines


# --- Twilio ---------------------------------------------------------------------------

TWILIO = "https://api.twilio.com"
TWILIO_SID = re.compile(r"^AC[0-9a-f]{32}$")
TWILIO_KEY = re.compile(r"^SK[0-9a-f]{32}$")


def twilio_month(
    http: httpx.Client, cfg: dict[str, Any], secret: dict[str, Any], month: date
) -> list[CostLine]:
    sid = cfg["account_sid"]
    if not TWILIO_SID.match(sid):
        raise ConnectorError("the Account SID starts with AC and has 32 hex characters")
    if not TWILIO_KEY.match(secret["key_sid"]):
        raise ConnectorError("the API key SID starts with SK")
    last = min(_next_month(month) - timedelta(days=1), utcnow().date())
    if last < month:
        return []
    auth = (secret["key_sid"], secret["key_secret"])
    url: str | None = f"{TWILIO}/2010-04-01/Accounts/{sid}/Usage/Records/Daily.json"
    params: dict[str, Any] | None = {
        "StartDate": month.isoformat(),
        "EndDate": last.isoformat(),
        "PageSize": 1000,
    }
    records: list[dict[str, Any]] = []
    for _ in range(MAX_PAGES):
        if url is None:
            break
        body = _call(http, "GET", url, auth=auth, params=params).json()
        records += body.get("usage_records") or []
        nxt = body.get("next_page_uri")
        url, params = (f"{TWILIO}{nxt}" if nxt and nxt.startswith("/") else None), None
    by_day: dict[str, dict[str, dict[str, Any]]] = {}
    for r in records:
        by_day.setdefault(str(r.get("start_date"))[:10], {})[str(r.get("category"))] = r
    lines: list[CostLine] = []
    for day, cats in sorted(by_day.items()):
        total = _dec((cats.get("totalprice") or {}).get("price")) or Decimal(0)
        names = [c for c in cats if c != "totalprice"]
        leaves = [c for c in names if not any(o != c and o.startswith(c + "-") for o in names)]
        spent = Decimal(0)
        currency = "USD"
        for c in leaves:
            r = cats[c]
            cost = _dec(r.get("price")) or Decimal(0)
            if cost == 0:
                continue
            currency = str(r.get("price_unit") or "usd").upper()[:3]
            spent += cost
            lines.append(
                CostLine(
                    charge_date=date.fromisoformat(day),
                    provider="twilio",
                    billing_account_id=sid,
                    service_name=str(r.get("description") or c)[:200],
                    usage_type=c,
                    consumed_quantity=_dec(r.get("usage")),
                    consumed_unit=r.get("usage_unit") or None,
                    billed_cost=cost,
                    effective_cost=cost,
                    billing_currency=currency,
                )
            )
        rest = total - spent
        if total and abs(rest) >= Decimal("0.01"):
            lines.append(
                CostLine(
                    charge_date=date.fromisoformat(day),
                    provider="twilio",
                    billing_account_id=sid,
                    service_name="Other Twilio usage",
                    usage_type="totalprice",
                    billed_cost=rest,
                    effective_cost=rest,
                    billing_currency=currency,
                )
            )
    return lines


# --- Sentry (estimate) ----------------------------------------------------------------

SENTRY_HOSTS = {
    "": "https://sentry.io",
    "sentry.io": "https://sentry.io",
    "us": "https://us.sentry.io",
    "de": "https://de.sentry.io",
}
SENTRY_ORG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,49}$")


def sentry_month(
    http: httpx.Client, cfg: dict[str, Any], secret: dict[str, Any], month: date
) -> list[CostLine]:
    org = str(cfg["org"])
    if not SENTRY_ORG.match(org):
        raise ConnectorError("not a Sentry organisation slug")
    host = SENTRY_HOSTS.get(str(cfg.get("region") or "").lower())
    if host is None:
        raise ConnectorError("the region is us or de (or empty)")
    rates = _rates(cfg.get("rates"))
    fee = _rate(cfg, "monthly_fee", "0")
    start, end = _month_bounds(month)
    end = min(end, datetime.now(UTC).replace(minute=0, second=0, microsecond=0))
    if end <= start:
        return []
    body = _call(
        http,
        "GET",
        f"{host}/api/0/organizations/{org}/stats_v2/",
        headers={"Authorization": f"Bearer {secret['token']}"},
        params={
            "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "interval": "1d",
            "field": "sum(quantity)",
            "groupBy": "category",
            "outcome": "accepted",
        },
    ).json()
    lines: list[CostLine] = []
    if fee:
        lines.append(
            CostLine(
                charge_date=month,
                provider="sentry",
                billing_account_id=org,
                service_name="Sentry plan",
                billed_cost=fee,
                effective_cost=fee,
                billing_currency="USD",
                extras={"estimate": True},
            )
        )
    for g in body.get("groups") or []:
        category = str((g.get("by") or {}).get("category") or "other")
        qty = Decimal(str((g.get("totals") or {}).get("sum(quantity)") or 0))
        if qty == 0:
            continue
        cost = (qty * rates.get(category, Decimal(0))).quantize(Decimal("0.0001"))
        lines.append(
            CostLine(
                charge_date=month,
                provider="sentry",
                billing_account_id=org,
                service_name=f"Sentry {category}s"[:200],
                usage_type=category,
                consumed_quantity=qty,
                consumed_unit="Events",
                billed_cost=cost,
                effective_cost=cost,
                billing_currency="USD",
                extras={"estimate": True, "rate": str(rates.get(category, 0))},
            )
        )
    return lines


# --- seat-priced tools (estimates) ----------------------------------------------------

OKTA_DOMAIN = re.compile(r"^[a-z0-9-]+\.(okta|oktapreview|okta-emea)\.com$")


def okta_month(
    http: httpx.Client, cfg: dict[str, Any], secret: dict[str, Any], month: date
) -> list[CostLine]:
    _current_month_only(month)
    domain = str(cfg["domain"]).lower()
    if not OKTA_DOMAIN.match(domain):
        raise ConnectorError("the domain is like acme.okta.com")
    price = _rate(cfg, "price_per_user")
    url: str | None = f"https://{domain}/api/v1/users"
    params: dict[str, Any] | None = {"filter": 'status eq "ACTIVE"', "limit": 200}
    seats = 0
    for _ in range(MAX_PAGES):
        if url is None:
            break
        r = _call(
            http,
            "GET",
            url,
            params=params,
            headers={"Authorization": f"SSWS {secret['token']}", "Accept": "application/json"},
        )
        seats += len(r.json() or [])
        nxt = r.links.get("next", {}).get("url")
        url = nxt if nxt and urlsplit(nxt).hostname == domain else None
        params = None
    return [_seat_line("okta", "Okta users", seats, price, month, domain)]


def slack_month(
    http: httpx.Client, cfg: dict[str, Any], secret: dict[str, Any], month: date
) -> list[CostLine]:
    _current_month_only(month)
    price = _rate(cfg, "price_per_user")
    seats, cursor, team = 0, "", None
    for _ in range(MAX_PAGES):
        body = _call(
            http,
            "GET",
            "https://slack.com/api/team.billableInfo",
            headers={"Authorization": f"Bearer {secret['token']}"},
            params={"cursor": cursor, "limit": 1000} if cursor else {"limit": 1000},
        ).json()
        if not body.get("ok"):
            raise ConnectorError(f"Slack answered {body.get('error') or 'an error'}", 403)
        info = body.get("billable_info") or {}
        seats += sum(1 for v in info.values() if (v or {}).get("billing_active"))
        cursor = (body.get("response_metadata") or {}).get("next_cursor") or ""
        if not cursor:
            break
    return [_seat_line("slack", "Slack seats", seats, price, month, team)]


def zoom_month(
    http: httpx.Client, cfg: dict[str, Any], secret: dict[str, Any], month: date
) -> list[CostLine]:
    _current_month_only(month)
    price = _rate(cfg, "price_per_user")
    token = (
        _call(
            http,
            "POST",
            "https://zoom.us/oauth/token",
            params={"grant_type": "account_credentials", "account_id": cfg["account_id"]},
            auth=(cfg["client_id"], secret["client_secret"]),
        )
        .json()
        .get("access_token")
    )
    if not token:
        raise ConnectorError("Zoom gave no token", 401)
    seats, page_token = 0, ""
    for _ in range(MAX_PAGES):
        params: dict[str, Any] = {"status": "active", "page_size": 300}
        if page_token:
            params["next_page_token"] = page_token
        body = _call(
            http,
            "GET",
            "https://api.zoom.us/v2/users",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
        ).json()
        seats += sum(1 for u in body.get("users") or [] if u.get("type") == 2)  # Licensed
        page_token = body.get("next_page_token") or ""
        if not page_token:
            break
    return [_seat_line("zoom", "Zoom licensed users", seats, price, month, cfg["account_id"])]


PAGERDUTY = {"us": "https://api.pagerduty.com", "eu": "https://api.eu.pagerduty.com"}


def pagerduty_month(
    http: httpx.Client, cfg: dict[str, Any], secret: dict[str, Any], month: date
) -> list[CostLine]:
    _current_month_only(month)
    base = PAGERDUTY.get(str(cfg.get("region") or "us").lower())
    if base is None:
        raise ConnectorError("the region is us or eu")
    full = _rate(cfg, "price_per_user")
    stakeholder = _rate(cfg, "price_per_stakeholder", "0")
    body = _call(
        http,
        "GET",
        f"{base}/licenses",
        headers={
            "Authorization": f"Token token={secret['token']}",
            "Accept": "application/vnd.pagerduty+json;version=2",
        },
    ).json()
    lines = []
    for lic in body.get("licenses") or []:
        seats = int(lic.get("current_value") or 0)
        if seats <= 0:
            continue
        price = stakeholder if str(lic.get("role_group")).lower() == "stakeholder" else full
        lines.append(
            _seat_line(
                "pagerduty",
                f"PagerDuty {lic.get('name') or 'licence'}"[:200],
                seats,
                price,
                month,
                None,
                str(lic.get("id") or "") or None,
            )
        )
    return lines


ATLASSIAN_SITE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


def atlassian_month(
    http: httpx.Client, cfg: dict[str, Any], secret: dict[str, Any], month: date
) -> list[CostLine]:
    _current_month_only(month)
    site = str(cfg["site"]).lower().removesuffix(".atlassian.net")
    if not ATLASSIAN_SITE.match(site):
        raise ConnectorError("the site is the name before .atlassian.net")
    price = _rate(cfg, "price_per_user")
    overrides = _rates(cfg.get("prices"))
    roles = _call(
        http,
        "GET",
        f"https://{site}.atlassian.net/rest/api/3/applicationrole",
        auth=(cfg["email"], secret["api_token"]),
        headers={"Accept": "application/json"},
    ).json()
    lines = []
    for role in roles or []:
        seats = int(role.get("userCount") or 0)
        key = str(role.get("key") or "")
        if seats <= 0 or key == "jira-core":  # Jira Work Management: part of the others
            continue
        lines.append(
            _seat_line(
                "atlassian",
                f"Atlassian {role.get('name') or key}"[:200],
                seats,
                overrides.get(key, price),
                month,
                site,
                key,
            )
        )
    return lines


EXTRA = {
    "oracle": Connector(
        "Oracle Cloud",
        ("tenancy_ocid", "user_ocid", "fingerprint", "region"),
        ("private_key",),
        oracle_month,
        "beta",
    ),
    "alibaba": Connector(
        "Alibaba Cloud", (), ("access_key_id", "access_key_secret"), alibaba_month, "beta"
    ),
    "hetzner": Connector("Hetzner Cloud", (), ("token",), hetzner_month, "alpha"),
    "twilio": Connector(
        "Twilio", ("account_sid",), ("key_sid", "key_secret"), twilio_month, "beta"
    ),
    "sentry": Connector("Sentry", ("org",), ("token",), sentry_month, "alpha"),
    "okta": Connector("Okta", ("domain", "price_per_user"), ("token",), okta_month, "alpha"),
    "slack": Connector("Slack", ("price_per_user",), ("token",), slack_month, "alpha"),
    "zoom": Connector(
        "Zoom",
        ("account_id", "client_id", "price_per_user"),
        ("client_secret",),
        zoom_month,
        "alpha",
    ),
    "pagerduty": Connector("PagerDuty", ("price_per_user",), ("token",), pagerduty_month, "alpha"),
    "atlassian": Connector(
        "Atlassian (Jira)",
        ("site", "email", "price_per_user"),
        ("api_token",),
        atlassian_month,
        "alpha",
    ),
}
EXTRA_OPTIONAL = {
    "alibaba": ("site",),
    "hetzner": ("project",),
    "sentry": ("region", "rates", "monthly_fee"),
    "pagerduty": ("region", "price_per_stakeholder"),
    "atlassian": ("prices",),
}
CONNECTORS.update(EXTRA)
OPTIONAL.update(EXTRA_OPTIONAL)
