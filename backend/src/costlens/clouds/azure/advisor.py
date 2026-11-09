"""Azure Advisor cost recommendations, imported like AWS's own advice (read-only).

Needs the Reader role on each subscription. Recommendations become
``ExternalRecommendation`` rows (source ``azure_advisor``); the imported-advice detector
turns those CostLens has no finding for into findings of their own."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete
from sqlalchemy.orm import Session

from costlens.clouds.azure.client import AzureClient, AzureError
from costlens.db.base import utcnow
from costlens.db.tenant import set_tenant
from costlens.models import CloudConnection, ExternalRecommendation

SOURCE = "azure_advisor"
MAX_PAGES = 20


def _action(text: str) -> str:
    t = text.lower()
    if "reserved" in t or "reservation" in t or "savings plan" in t:
        return "purchase"
    if "right-size" in t or "rightsize" in t or "resize" in t or "shutdown" in t:
        return "rightsize"
    if "delete" in t or "unused" in t or "idle" in t or "unattached" in t:
        return "delete"
    return "other"


def subscriptions(client: AzureClient, scope: str) -> list[str]:
    if scope.startswith("/subscriptions/"):
        return [scope.split("/")[2]]
    subs = client.request("GET", "/subscriptions?api-version=2022-12-01").json().get("value") or []
    return [s["subscriptionId"] for s in subs if s.get("state", "Enabled") == "Enabled"]


def recommendations(client: AzureClient, subscription: str) -> list[dict[str, Any]]:
    url = (
        f"/subscriptions/{subscription}/providers/Microsoft.Advisor/recommendations"
        "?api-version=2023-01-01&$filter=Category eq 'Cost'"
    )
    out: list[dict[str, Any]] = []
    for _ in range(MAX_PAGES):
        body = client.request("GET", url).json()
        for rec in body.get("value") or []:
            p = rec.get("properties") or {}
            ext = p.get("extendedProperties") or {}
            short = p.get("shortDescription") or {}
            monthly = (
                float(ext.get("savingsAmount") or 0)
                or float(ext.get("annualSavingsAmount") or 0) / 12
            )
            resource = (p.get("resourceMetadata") or {}).get("resourceId") or rec.get(
                "id", ""
            ).split("/providers/Microsoft.Advisor")[0]
            out.append(
                {
                    "source_id": rec.get("name") or rec.get("id"),
                    "action": _action(f"{short.get('solution', '')} {short.get('problem', '')}"),
                    "title": short.get("solution")
                    or short.get("problem")
                    or "Azure Advisor recommendation",
                    "savings_monthly": round(monthly, 2),
                    "currency": (ext.get("savingsCurrency") or "USD")[:3],
                    "account_id": subscription,
                    "region": ext.get("region") or ext.get("location"),
                    "resource_id": resource,
                    "resource_type": p.get("impactedField"),
                    "data": {
                        "impact": p.get("impact"),
                        "problem": short.get("problem"),
                        "current_sku": ext.get("currentSku"),
                        "target_sku": ext.get("targetSku"),
                        "effort": "Low" if p.get("impact") == "Low" else "Medium",
                    },
                }
            )
        url = body.get("nextLink")
        if not url:
            break
    return out


def collect(db: Session, client: AzureClient, connection_id: uuid.UUID) -> dict[str, Any]:
    set_tenant(db, bypass=True)
    conn = db.get(CloudConnection, connection_id)
    if conn is None or conn.provider != "azure" or conn.status != "active":
        return {}
    org_id = conn.org_id
    found: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    try:
        subs = subscriptions(client, (conn.settings or {}).get("scope", ""))
    except AzureError as exc:
        return {"errors": {"subscriptions": str(exc)[:200]}}
    for sub in subs:
        try:
            found += recommendations(client, sub)
        except AzureError as exc:
            errors[sub] = str(exc)[:200]
    set_tenant(db, org_id=org_id)
    if not errors or found:
        db.execute(
            delete(ExternalRecommendation).where(
                ExternalRecommendation.connection_id == conn.id,
                ExternalRecommendation.source == SOURCE,
            )
        )
    now, seen = utcnow(), set()
    for a in found:
        if a["source_id"] in seen:
            continue
        seen.add(a["source_id"])
        db.add(
            ExternalRecommendation(
                org_id=org_id,
                connection_id=conn.id,
                source=SOURCE,
                source_id=str(a["source_id"])[:500],
                account_id=a["account_id"],
                region=a["region"],
                resource_id=(a["resource_id"] or "")[:1000] or None,
                resource_type=(a["resource_type"] or "")[:100] or None,
                action=a["action"],
                title=a["title"][:500],
                savings_monthly=a["savings_monthly"],
                currency=a["currency"],
                data=a["data"],
                fetched_at=now,
            )
        )
    conn.settings = {
        **(conn.settings or {}),
        "advice": {"fetched_at": now.isoformat(), "errors": errors, "count": len(seen)},
    }
    db.flush()
    return {"count": len(seen), "errors": errors}
