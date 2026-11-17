"""Azure and Google Cloud inventory (11.28): managed disks and public IP addresses, the
two resources most often left behind and billed for nothing.

Read-only, with the cost connection's own credentials (Reader on Azure; Compute Viewer
on Google Cloud). Resources not seen in a successful collection are marked deleted.
Oversized machines and reservations come from Azure Advisor and Google Recommender,
which are imported separately."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ccradar.db.base import utcnow
from ccradar.db.tenant import set_tenant
from ccradar.inventory.aws import Found
from ccradar.inventory.service import upsert
from ccradar.models import CloudConnection, CloudResource

MAX_PAGES = 50
COMPUTE = "https://compute.googleapis.com/compute/v1/projects"
TYPES = ("managed_disk", "public_ip")


def _when(text: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")) if text else None
    except ValueError:
        return None


def _azure_list(client: Any, url: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for _ in range(MAX_PAGES):
        page = client.request("GET", url).json()
        out += page.get("value") or []
        url = page.get("nextLink") or ""
        if not url:
            break
    return out


def azure_found(client: Any, subscription: str) -> list[Found]:
    base = f"/subscriptions/{subscription}/providers"
    found = []
    for d in _azure_list(client, f"{base}/Microsoft.Compute/disks?api-version=2023-04-02"):
        p = d.get("properties") or {}
        found.append(Found(
            "Azure Managed Disks", "managed_disk", str(d["id"]).lower(), str(d.get("location")),
            name=d.get("name"), state=str(p.get("diskState") or "").lower() or None,
            created_at=_when(p.get("timeCreated")), tags=dict(d.get("tags") or {}),
            attributes={"size_gb": p.get("diskSizeGB"), "sku": (d.get("sku") or {}).get("name")},
        ))  # fmt: skip
    url = f"{base}/Microsoft.Network/publicIPAddresses?api-version=2023-09-01"
    for ip in _azure_list(client, url):
        p = ip.get("properties") or {}
        used = bool(p.get("ipConfiguration") or p.get("natGateway"))
        found.append(Found(
            "Azure Public IP", "public_ip", str(ip["id"]).lower(), str(ip.get("location")),
            name=ip.get("name"), state="in-use" if used else "unattached",
            tags=dict(ip.get("tags") or {}),
            attributes={"sku": (ip.get("sku") or {}).get("name"), "address": p.get("ipAddress"),
                        "allocation": p.get("publicIPAllocationMethod")},
        ))  # fmt: skip
    return found


def _gcp_aggregated(client: Any, project: str, kind: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    params: dict[str, Any] = {"maxResults": 500}
    for _ in range(MAX_PAGES):
        page = client.request("GET", f"{COMPUTE}/{project}/aggregated/{kind}", params=params)
        for scoped in (page.get("items") or {}).values():
            out += scoped.get(kind) or []
        token = page.get("nextPageToken")
        if not token:
            break
        params["pageToken"] = token
    return out


def _last(url: Any) -> str:
    return str(url or "").rstrip("/").rsplit("/", 1)[-1]


def gcp_found(client: Any, project: str) -> list[Found]:
    found = []
    for d in _gcp_aggregated(client, project, "disks"):
        zone = _last(d.get("zone"))
        found.append(Found(
            "Compute Engine", "managed_disk", str(d.get("selfLink") or d.get("id")),
            zone[:-2] if zone[-2:-1] == "-" else zone, name=d.get("name"),
            state="in-use" if d.get("users") else "unattached",
            created_at=_when(d.get("creationTimestamp")), tags=dict(d.get("labels") or {}),
            attributes={"size_gb": float(d.get("sizeGb") or 0), "sku": _last(d.get("type")),
                        "zone": zone},
        ))  # fmt: skip
    for a in _gcp_aggregated(client, project, "addresses"):
        if a.get("addressType", "EXTERNAL") != "EXTERNAL":
            continue  # internal addresses are free
        found.append(Found(
            "Compute Engine", "public_ip", str(a.get("selfLink") or a.get("id")),
            _last(a.get("region")) or "global", name=a.get("name"),
            state="in-use" if a.get("status") == "IN_USE" else "unattached",
            tags=dict(a.get("labels") or {}),
            attributes={"address": a.get("address"), "sku": a.get("networkTier")},
        ))  # fmt: skip
    return found


def _mark_gone(db: Session, provider: str, account: str, before: datetime) -> int:
    rows = db.scalars(
        select(CloudResource).where(
            CloudResource.provider == provider,
            CloudResource.account_id == account,
            CloudResource.resource_type.in_(TYPES),
            CloudResource.last_seen_at < before,
            CloudResource.state != "deleted",
        )
    ).all()
    for r in rows:
        r.state = "deleted"
    return len(rows)


def collect(db: Session, client: Any, connection_id: uuid.UUID,
            now: datetime | None = None) -> dict[str, int]:  # fmt: skip
    """Collect disks and public IPs for one Azure or Google Cloud connection."""
    set_tenant(db, bypass=True)
    conn = db.get(CloudConnection, connection_id)
    if conn is None or conn.provider not in ("azure", "gcp"):
        return {}
    org_id, now = conn.org_id, now or utcnow()
    set_tenant(db, org_id=org_id)
    counts: dict[str, int] = {}
    if conn.provider == "azure":
        from ccradar.clouds.azure.advisor import subscriptions

        accounts = subscriptions(client, str((conn.settings or {}).get("scope") or ""))
    else:
        from ccradar.clouds.gcp.recommender import locations

        accounts = sorted({p for p, _, _ in locations(db, org_id)})
    for account in accounts:
        items = (
            azure_found(client, account) if conn.provider == "azure" else gcp_found(client, account)
        )
        counts[account] = upsert(db, org_id, account, items, provider=conn.provider, seen_at=now)
        _mark_gone(db, conn.provider, account, now)
    db.flush()
    return counts
