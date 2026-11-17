"""Live Radar for Azure and Google Cloud (10.2).

- **Azure:** the Activity Log of each subscription the connection can read. Virtual
  machines created or resized are looked up with Resource Manager for their size and
  location, and priced with the public Azure Retail Prices API (pay-as-you-go Linux list
  price, no sign-in needed). Deletions, deallocations and other writes are recorded with
  who made them, unpriced.
- **Google Cloud:** Admin Activity audit logs of each project in the bill (Cloud
  Logging). Instances created are priced from their machine type's vCPUs and memory at
  us-central1 list rates; changes of machine type and deletions are recorded unpriced.

Both reuse the cost connection's read-only credentials (Reader on Azure; Logs Viewer on
Google Cloud, which the setup guide asks for) and record changes the same way as AWS, so
the feed, alerts, budget guardrails and policies work unchanged. Each scan reads the
last few hours; events already seen are updated, not duplicated.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.orm import Session

from ccradar.changes import radar
from ccradar.changes.service import ChangeIn, record
from ccradar.db.base import utcnow
from ccradar.db.tenant import set_tenant
from ccradar.models import CloudConnection

log = logging.getLogger(__name__)
HOURS = 730.0
LOOKBACK = timedelta(hours=3)
MAX_EVENTS = 2000
RETAIL = "https://prices.azure.com/api/retail/prices"
AZURE_SIZE = re.compile(r"^[A-Za-z0-9_]{3,60}$")
AZURE_REGION = re.compile(r"^[a-z0-9]{3,30}$")
AZURE_OPS = {
    "microsoft.compute/virtualmachines/write": "VM created or changed",
    "microsoft.compute/virtualmachines/delete": "VM deleted",
    "microsoft.compute/virtualmachines/deallocate/action": "VM deallocated",
    "microsoft.compute/virtualmachines/start/action": "VM started",
    "microsoft.compute/disks/write": "Disk created or changed",
    "microsoft.compute/disks/delete": "Disk deleted",
    "microsoft.network/publicipaddresses/write": "Public IP created or changed",
    "microsoft.network/natgateways/write": "NAT gateway created or changed",
    "microsoft.sql/servers/databases/write": "SQL database created or changed",
}
NEW_WITHIN = timedelta(minutes=30)  # a VM created this close to the event is new
_azure_prices: dict[tuple[str, str], float | None] = {}


# --- Azure -----------------------------------------------------------------------------


def azure_vm_hourly(http: httpx.Client, size: str, region: str) -> float | None:
    """Pay-as-you-go Linux list price per hour, USD (cached per process)."""
    key = (size, region)
    if key in _azure_prices:
        return _azure_prices[key]
    price = None
    if AZURE_SIZE.match(size) and AZURE_REGION.match(region):
        query = (
            f"serviceName eq 'Virtual Machines' and armSkuName eq '{size}' "
            f"and armRegionName eq '{region}' and priceType eq 'Consumption'"
        )
        try:
            r = http.get(RETAIL, params={"$filter": query}, timeout=30.0)
            r.raise_for_status()
            items = [
                i
                for i in r.json().get("Items") or []
                if i.get("unitOfMeasure") == "1 Hour"
                and "Windows" not in str(i.get("productName"))
                and not re.search(r"Spot|Low Priority", str(i.get("skuName")))
            ]
            price = min(float(i["retailPrice"]) for i in items) if items else None
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            log.warning("Azure retail price for %s in %s: %s", size, region, type(exc).__name__)
            return None  # not cached: try again next scan
    _azure_prices[key] = price
    return price


def _azure_vm_impact(client: Any, http: httpx.Client, resource_id: str, at: datetime,
                     pricing: radar.Pricing) -> dict[str, Any] | None:  # fmt: skip
    try:
        vm = client.request("GET", f"{resource_id}?api-version=2024-07-01").json()
    except Exception:  # gone already, or no rights: recorded unpriced
        return None
    props = vm.get("properties") or {}
    size = (props.get("hardwareProfile") or {}).get("vmSize")
    created = props.get("timeCreated")
    if not size or not created:
        return None
    if abs(datetime.fromisoformat(str(created).replace("Z", "+00:00")) - at) > NEW_WITHIN:
        return None  # an update of an existing VM: its old size is unknown
    hourly = azure_vm_hourly(http, str(size), str(vm.get("location") or ""))
    if hourly is None:
        return None
    monthly = hourly * HOURS * pricing.factor
    return {"monthly": round(monthly, 2), "currency": pricing.currency,
            "source": "list price", "label": f"1 x {size}"}  # fmt: skip


def azure_changes(client: Any, http: httpx.Client, subscription: str, start: datetime,
                  end: datetime, pricing: radar.Pricing) -> list[ChangeIn]:  # fmt: skip
    since, until = (t.strftime("%Y-%m-%dT%H:%M:%SZ") for t in (start, end))
    url = (
        f"/subscriptions/{subscription}/providers/Microsoft.Insights/eventtypes/management/"
        f"values?api-version=2015-04-01&$filter=eventTimestamp ge '{since}' and "
        f"eventTimestamp le '{until}'"
    )
    out: list[ChangeIn] = []
    seen = 0
    while url and seen < MAX_EVENTS:
        page = client.request("GET", url).json()
        for e in page.get("value") or []:
            seen += 1
            op = str((e.get("operationName") or {}).get("value") or "").lower()
            if op not in AZURE_OPS or (e.get("status") or {}).get("value") != "Succeeded":
                continue
            at = datetime.fromisoformat(str(e["eventTimestamp"]).replace("Z", "+00:00"))
            rid = str(e.get("resourceId") or "")
            impact = (
                _azure_vm_impact(client, http, rid, at, pricing)
                if op == "microsoft.compute/virtualmachines/write"
                else None
            )
            out.append(
                ChangeIn(
                    occurred_at=at,
                    kind="infrastructure",
                    title=f"{AZURE_OPS[op]}: {rid.rsplit('/', 1)[-1]}"[:300],
                    service=str((e.get("resourceProviderName") or {}).get("value") or "Azure"),
                    account=subscription,
                    region=None,
                    resource_id=rid[:1000] or None,
                    actor=(str(e.get("caller") or "") or None),
                    external_id=str(e.get("eventDataId")),
                    attributes={
                        "event_source": "azure",
                        "event_name": op,
                        **({"impact": impact} if impact else {}),
                    },
                )
            )
        url = page.get("nextLink") or ""
    return out


# --- Google Cloud ------------------------------------------------------------------------

# us-central1 on-demand list prices per vCPU-hour and GB-hour (please verify periodically).
GCP_RATES = {
    "e2": (0.021811, 0.002923),
    "n1": (0.031611, 0.004237),
    "n2": (0.031611, 0.004237),
    "n2d": (0.027502, 0.003686),
    "t2d": (0.027502, 0.003686),
    "c2": (0.03398, 0.00455),
}
GCP_MEM = {"standard": 4.0, "highmem": 8.0, "highcpu": 1.0}
GCP_N1_MEM = {"standard": 3.75, "highmem": 6.5, "highcpu": 0.9}
GCP_SHARED = {"e2-micro": 0.008376, "e2-small": 0.016751, "e2-medium": 0.033503,
              "f1-micro": 0.0076, "g1-small": 0.0257}  # fmt: skip
GCP_PROJECT = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")


def gcp_hourly(machine_type: str) -> float | None:
    name = machine_type.rsplit("/", 1)[-1]
    if name in GCP_SHARED:
        return GCP_SHARED[name]
    custom = re.match(r"^(?:(\w+)-)?custom-(\d+)-(\d+)(?:-ext)?$", name)
    if custom:
        rates = GCP_RATES.get(custom[1] or "n1")
        return rates[0] * int(custom[2]) + rates[1] * int(custom[3]) / 1024 if rates else None
    m = re.match(r"^(\w+)-(standard|highmem|highcpu)-(\d+)$", name)
    if not m or m[1] not in GCP_RATES:
        return None
    vcpus = int(m[3])
    per_cpu = (GCP_N1_MEM if m[1] == "n1" else GCP_MEM)[m[2]]
    cpu, mem = GCP_RATES[m[1]]
    return cpu * vcpus + mem * vcpus * per_cpu


GCP_METHODS = {
    "compute.instances.insert": "Instance created",
    "compute.instances.delete": "Instance deleted",
    "compute.instances.setMachineType": "Machine type changed",
    "compute.instances.stop": "Instance stopped",
    "compute.instances.start": "Instance started",
    "compute.disks.insert": "Disk created",
    "compute.disks.delete": "Disk deleted",
}


def gcp_changes(client: Any, project: str, start: datetime, end: datetime,
                pricing: radar.Pricing) -> list[ChangeIn]:  # fmt: skip
    if not GCP_PROJECT.match(project):
        return []
    methods = " OR ".join(f'"{m}"' for m in GCP_METHODS)
    body: dict[str, Any] = {
        "resourceNames": [f"projects/{project}"],
        "filter": (
            f'logName="projects/{project}/logs/cloudaudit.googleapis.com%2Factivity" '
            f"AND protoPayload.methodName:({methods}) "
            f'AND timestamp>="{start.strftime("%Y-%m-%dT%H:%M:%SZ")}" '
            f'AND timestamp<="{end.strftime("%Y-%m-%dT%H:%M:%SZ")}"'
        ),
        "orderBy": "timestamp desc",
        "pageSize": 1000,
    }
    out: list[ChangeIn] = []
    seen = 0
    while seen < MAX_EVENTS:
        page = client.request("POST", "https://logging.googleapis.com/v2/entries:list", json=body)
        for entry in page.get("entries") or []:
            seen += 1
            p = entry.get("protoPayload") or {}
            method = next(
                (m for m in GCP_METHODS if str(p.get("methodName", "")).endswith(m)), None
            )
            if method is None or (p.get("status") or {}).get("code"):
                continue
            if (entry.get("operation") or {}).get("last") and not (
                entry.get("operation") or {}
            ).get("first"):
                continue  # the closing entry of a long operation: the first one is enough
            resource = str(p.get("resourceName") or "")
            zone = re.search(r"/zones/([a-z0-9-]+)/", f"/{resource}/")
            impact = None
            if method == "compute.instances.insert":
                machine = str((p.get("request") or {}).get("machineType") or "")
                hourly = gcp_hourly(machine) if machine else None
                if hourly is not None:
                    impact = {"monthly": round(hourly * HOURS * pricing.factor, 2),
                              "currency": pricing.currency, "source": "list price",
                              "label": f"1 x {machine.rsplit('/', 1)[-1]}"}  # fmt: skip
            out.append(
                ChangeIn(
                    occurred_at=datetime.fromisoformat(
                        str(entry["timestamp"]).replace("Z", "+00:00")
                    ),
                    kind="infrastructure",
                    title=f"{GCP_METHODS[method]}: {resource.rsplit('/', 1)[-1]}"[:300],
                    service="Compute Engine",
                    account=project,
                    region=zone[1][:-2] if zone else None,
                    resource_id=resource[:1000] or None,
                    actor=((p.get("authenticationInfo") or {}).get("principalEmail") or None),
                    external_id=str(entry.get("insertId")),
                    attributes={
                        "event_source": "gcp",
                        "event_name": method,
                        **({"impact": impact} if impact else {}),
                    },
                )
            )
        token = page.get("nextPageToken")
        if not token:
            break
        body["pageToken"] = token
    return out


# --- scan ----------------------------------------------------------------------------------


def import_connection(db: Session, connection_id: uuid.UUID, *, azure_client: Any = None,
                      gcp_client: Any = None, http: httpx.Client | None = None,
                      now: datetime | None = None) -> int:  # fmt: skip
    """Read one Azure or Google Cloud connection's recent changes; returns new changes."""
    from ccradar.core.notify import outbound

    set_tenant(db, bypass=True)
    conn = db.get(CloudConnection, connection_id)
    if conn is None or conn.status != "active" or conn.provider not in ("azure", "gcp"):
        return 0
    org_id, now = conn.org_id, now or utcnow()
    start = now - LOOKBACK
    set_tenant(db, org_id=org_id)
    pricing = radar.pricing_for(db, org_id)
    changes: list[ChangeIn] = []
    problems: list[str] = []
    try:
        if conn.provider == "azure":
            from ccradar.clouds.azure.advisor import subscriptions
            from ccradar.clouds.azure.client import for_connection

            client = azure_client or for_connection(conn)
            for sub in subscriptions(client, str((conn.settings or {}).get("scope") or "")):
                changes += azure_changes(client, http or outbound()[0], sub, start, now, pricing)
        else:
            from ccradar.clouds.gcp.client import for_connection as gcp_for
            from ccradar.clouds.gcp.recommender import locations

            client = gcp_client or gcp_for(conn)
            for project in sorted({p for p, _, _ in locations(db, org_id)}):
                changes += gcp_changes(client, project, start, now, pricing)
    except Exception as exc:  # no rights to the logs yet, throttling…: say so, keep going
        problems.append(f"{type(exc).__name__}: {str(exc)[:200]}")
    fresh = []
    for change in changes:
        event, is_new = record(db, org_id, change, source=f"{conn.provider}_activity")
        if is_new:
            fresh.append(event)
    set_tenant(db, bypass=True)
    conn.settings = {**(conn.settings or {}), "radar_scan": {
        "at": now.isoformat(), "complete": not problems, "problems": problems,
        "events_read": len(changes)}}  # fmt: skip
    set_tenant(db, org_id=org_id)
    radar.alert(db, org_id, fresh)
    db.flush()
    return len(fresh)
