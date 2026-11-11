"""Google Cloud Recommender cost recommendations, imported like AWS's and Azure's.

Recommenders are per project and location. The projects, zones and regions to ask come
from the last 30 days of the Google Cloud bill, so CloudCostRadar only asks where there is
spend (at most ``MAX_LOCATIONS`` places per run). Needs the Recommender Viewer roles."""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ccradar.clouds.gcp.client import GcpClient, GcpError
from ccradar.db.base import utcnow
from ccradar.db.tenant import set_tenant
from ccradar.models import CloudConnection, CostLineItem, ExternalRecommendation

SOURCE = "gcp_recommender"
ZONAL = (
    "google.compute.instance.IdleResourceRecommender",
    "google.compute.instance.MachineTypeRecommender",
    "google.compute.disk.IdleResourceRecommender",
)
REGIONAL = (
    "google.compute.address.IdleResourceRecommender",
    "google.compute.commitment.UsageCommitmentRecommender",
)
ACTIONS = {
    "STOP_VM": "stop",
    "CHANGE_MACHINE_TYPE": "rightsize",
    "DELETE_DISK": "delete",
    "SNAPSHOT_AND_DELETE_DISK": "delete",
    "DELETE_ADDRESS": "delete",
}
MAX_LOCATIONS = 50
DAY_SECONDS = 86400


def locations(db: Session, org_id: uuid.UUID) -> list[tuple[str, str, tuple[str, ...]]]:
    """(project, location, recommenders) where the bill shows Google Cloud spend."""
    since = utcnow().date() - timedelta(days=30)
    rows = db.execute(
        select(CostLineItem.sub_account_id, CostLineItem.availability_zone, CostLineItem.region)
        .where(
            CostLineItem.org_id == org_id,
            CostLineItem.provider == "gcp",
            CostLineItem.charge_date >= since,
            CostLineItem.sub_account_id.is_not(None),
        )
        .distinct()
    ).all()
    out: set[tuple[str, str, tuple[str, ...]]] = set()
    for project, zone, region in rows:
        if not project:
            continue
        if zone:
            out.add((project, zone, ZONAL))
        if region:
            out.add((project, region, REGIONAL))
    return sorted(out)[:MAX_LOCATIONS]


def _monthly(impact: dict[str, Any]) -> tuple[float, str]:
    proj = impact.get("costProjection") or {}
    cost = proj.get("cost") or {}
    amount = Decimal(str(cost.get("units") or 0)) + Decimal(int(cost.get("nanos") or 0)) / Decimal(
        10**9
    )
    seconds = float(str(proj.get("duration") or "2592000s").rstrip("s") or 2592000)
    monthly = float(-amount) * (30 * DAY_SECONDS) / seconds if seconds else 0.0
    return round(max(monthly, 0.0), 2), (cost.get("currencyCode") or "USD")[:3]


def recommendations(
    client: GcpClient, project: str, location: str, recommender: str
) -> list[dict[str, Any]]:
    url = (
        f"https://recommender.googleapis.com/v1/projects/{project}/locations/{location}"
        f"/recommenders/{recommender}/recommendations"
    )
    out = []
    token = None
    for _ in range(10):
        params = {"filter": "stateInfo.state = ACTIVE", "pageSize": 500}
        if token:
            params["pageToken"] = token
        body = client.request("GET", url, params=params)
        for rec in body.get("recommendations") or []:
            impact = rec.get("primaryImpact") or {}
            if impact.get("category") != "COST":
                continue
            monthly, currency = _monthly(impact)
            overview = (rec.get("content") or {}).get("overview") or {}
            targets = rec.get("targetResources") or []
            resource = (
                overview.get("resource")
                or overview.get("resourceName")
                or (targets[0] if targets else None)
            )
            subtype = rec.get("recommenderSubtype") or ""
            out.append(
                {
                    "source_id": rec.get("name"),
                    "action": "purchase"
                    if "commitment" in recommender.lower()
                    else ACTIONS.get(subtype, "other"),
                    "title": rec.get("description") or subtype or "Google Cloud recommendation",
                    "savings_monthly": monthly,
                    "currency": currency,
                    "account_id": project,
                    "region": location,
                    "resource_id": resource,
                    "resource_type": recommender.split(".")[2]
                    if recommender.count(".") >= 3
                    else None,
                    "data": {
                        "subtype": subtype,
                        "recommender": recommender,
                        "priority": rec.get("priority"),
                    },
                }
            )
        token = body.get("nextPageToken")
        if not token:
            break
    return out


def collect(db: Session, client: GcpClient, connection_id: uuid.UUID) -> dict[str, Any]:
    set_tenant(db, bypass=True)
    conn = db.get(CloudConnection, connection_id)
    if conn is None or conn.provider != "gcp" or conn.status != "active":
        return {}
    org_id = conn.org_id
    set_tenant(db, org_id=org_id)
    found: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    for project, location, recommenders in locations(db, org_id):
        for rid in recommenders:
            try:
                found += recommendations(client, project, location, rid)
            except GcpError as exc:
                if exc.status != 404:  # not every recommender exists in every location
                    errors[f"{project}/{location}/{rid.split('.')[-1]}"] = str(exc)[:200]
    if found or not errors:
        db.execute(
            delete(ExternalRecommendation).where(
                ExternalRecommendation.connection_id == conn.id,
                ExternalRecommendation.source == SOURCE,
            )
        )
    now, seen = utcnow(), set()
    for a in found:
        if not a["source_id"] or a["source_id"] in seen:
            continue
        seen.add(a["source_id"])
        db.add(
            ExternalRecommendation(
                org_id=org_id,
                connection_id=conn.id,
                source=SOURCE,
                source_id=str(a["source_id"])[:500],
                account_id=(a["account_id"] or "")[:64] or None,
                region=(a["region"] or "")[:40] or None,
                resource_id=(a["resource_id"] or "")[:1000] or None,
                resource_type=a["resource_type"],
                action=a["action"],
                title=str(a["title"])[:500],
                savings_monthly=a["savings_monthly"],
                currency=a["currency"],
                data=a["data"],
                fetched_at=now,
            )
        )
    set_tenant(db, bypass=True)
    conn.settings = {
        **(conn.settings or {}),
        "advice": {"fetched_at": now.isoformat(), "errors": errors, "count": len(seen)},
    }
    db.flush()
    return {"count": len(seen), "errors": errors}
