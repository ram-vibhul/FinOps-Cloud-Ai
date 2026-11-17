"""Azure and Google Cloud commitments (11.22), in the same inventory as AWS's.

- **Azure**: reservations (``Microsoft.Capacity/reservations``) and savings plans
  (``Microsoft.BillingBenefits/savingsPlans``), with Azure's own 30-day utilisation.
  Both are listed at tenant level and need the *Reservations Reader* and *Savings plan
  Reader* roles; without them the rest of the connection still works.
- **Google Cloud**: resource-based committed use discounts per project
  (``compute/v1/projects/<p>/aggregated/commitments``, covered by Compute Viewer).
  Spend-based (flexible) commitments live on the billing account and are not listed by
  this API; Google reports no utilisation here either, so it is left blank.

Read-only GET calls; expiry alerts (90/30/7 days) come from the shared job.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ccradar.clouds.aws.commitment_inventory import Item, _dt, _f
from ccradar.db.base import utcnow
from ccradar.db.tenant import set_tenant
from ccradar.inventory.other_clouds import _azure_list, _gcp_aggregated, _last
from ccradar.models import CloudConnection, Commitment

log = logging.getLogger(__name__)

RESERVATIONS = "/providers/Microsoft.Capacity/reservations?api-version=2022-11-01"
SAVINGS_PLANS = "/providers/Microsoft.BillingBenefits/savingsPlans?api-version=2022-11-01"
TERM = re.compile(r"^P(\d+)([YM])$")
AZURE_STATE = {
    "succeeded": "active",
    "expiring": "active",
    "pendingbilling": "payment-pending",
    "pending": "queued",
    "creating": "queued",
}
GCP_STATE = {"ACTIVE": "active", "NOT_YET_ACTIVE": "queued", "CREATING": "queued"}
GCP_TERM = {"TWELVE_MONTH": 1.0, "THIRTY_SIX_MONTH": 3.0}


def _term(value: Any) -> float | None:
    m = TERM.match(str(value or ""))
    if not m:
        return None
    n = int(m[1])
    return float(n) if m[2] == "Y" else round(n / 12, 1)


def _azure_state(p: dict[str, Any]) -> str:
    raw = str(p.get("displayProvisioningState") or p.get("provisioningState") or "").lower()
    return AZURE_STATE.get(raw, "retired")  # expired, cancelled, failed, split, merged


def _azure_used(p: dict[str, Any]) -> float | None:
    """Azure's own utilisation: the 30-day figure, else the 7-day, else the 1-day."""
    by_grain = {
        _f(a.get("grain")): _f(a.get("value"))
        for a in (p.get("utilization") or {}).get("aggregates") or []
        if str(a.get("valueUnit", "percentage")).lower() == "percentage"
    }
    for grain in (30.0, 7.0, 1.0):
        if by_grain.get(grain) is not None:
            return by_grain[grain]
    return None


def _payment(plan: Any) -> str | None:
    return {"Upfront": "All Upfront", "P1M": "Monthly"}.get(str(plan), str(plan) if plan else None)


def azure_items(client: Any) -> tuple[list[Item], dict[str, str], set[str]]:
    items: list[Item] = []
    errors: dict[str, str] = {}
    complete: set[str] = set()
    try:
        for r in _azure_list(client, RESERVATIONS):
            p = r.get("properties") or {}
            sku = (r.get("sku") or {}).get("name") or p.get("skuDescription") or ""
            items.append(Item(
                "azure_reservation", str(r["id"]).lower(),
                f"{p.get('reservedResourceType') or ''} {sku}".strip(), r.get("location"),
                _azure_state(p), _dt(p.get("effectiveDateTime") or p.get("purchaseDate")),
                _dt(p.get("expiryDateTimeInUtc") or p.get("expiryDateTime")),
                count=int(_f(p.get("quantity")) or 1), term_years=_term(p.get("term")),
                payment_option=_payment(p.get("billingPlan")),
                data={k: p[k] for k in ("displayName", "appliedScopeType", "renew") if k in p}
                | {"utilization": _azure_used(p)},
            ))  # fmt: skip
        complete.add("azure_reservation")
    except Exception as error:  # needs Reservations Reader at tenant level
        errors["azure_reservations"] = type(error).__name__
        log.info("Azure reservations unavailable: %s", type(error).__name__)
    try:
        for s in _azure_list(client, SAVINGS_PLANS):
            p = s.get("properties") or {}
            commit = p.get("commitment") or {}
            hourly = _f(commit.get("amount")) if commit.get("grain", "Hourly") == "Hourly" else None
            items.append(Item(
                "azure_savings_plan", str(s["id"]).lower(),
                str(p.get("displayName") or (s.get("sku") or {}).get("name") or "Savings plan"),
                None, _azure_state(p), _dt(p.get("effectiveDateTime") or p.get("benefitStartTime")),
                _dt(p.get("expiryDateTime")), hourly_commitment=hourly,
                currency=str(commit.get("currencyCode") or "USD"), term_years=_term(p.get("term")),
                payment_option=_payment(p.get("billingPlan")),
                data={"appliedScopeType": p.get("appliedScopeType"),
                      "utilization": _azure_used(p)},
            ))  # fmt: skip
        complete.add("azure_savings_plan")
    except Exception as error:  # needs Savings plan Reader at tenant level
        errors["azure_savings_plans"] = type(error).__name__
        log.info("Azure savings plans unavailable: %s", type(error).__name__)
    return items, errors, complete


def _gcp_describe(c: dict[str, Any]) -> str:
    parts = []
    for r in c.get("resources") or []:
        amount = _f(r.get("amount")) or 0
        kind = str(r.get("type") or "")
        if kind == "MEMORY":
            parts.append(f"{amount / 1024:g} GB memory")
        elif kind == "VCPU":
            parts.append(f"{amount:g} vCPU")
        elif amount:
            parts.append(f"{amount:g} {kind.lower().replace('_', ' ')}")
    family = str(c.get("type") or "").replace("_", " ").title()
    return f"{family} {', '.join(parts)}".strip() or str(c.get("name") or "Commitment")


def gcp_items(client: Any, projects: list[str]) -> tuple[list[Item], dict[str, str], set[str]]:
    items: list[Item] = []
    errors: dict[str, str] = {}
    ok = True
    for project in projects:
        try:
            for c in _gcp_aggregated(client, project, "commitments"):
                item = Item(
                    "gcp_cud",
                    str(c.get("selfLink") or c.get("id")),
                    _gcp_describe(c),
                    _last(c.get("region")) or None,
                    GCP_STATE.get(str(c.get("status")), "retired"),
                    _dt(c.get("startTimestamp")),
                    _dt(c.get("endTimestamp")),
                    term_years=GCP_TERM.get(str(c.get("plan"))),
                    payment_option="Monthly",
                    data={"project": project, "category": c.get("category"),
                          "auto_renew": bool(c.get("autoRenew")), "name": c.get("name")},
                )  # fmt: skip
                items.append(item)
        except Exception as error:  # needs Compute Viewer on the project
            errors[f"gcp:{project}"] = type(error).__name__
            log.info("Google Cloud commitments unavailable: %s", type(error).__name__)
            ok = False
    return items, errors, {"gcp_cud"} if ok else set()


def store(
    db: Session,
    conn: CloudConnection,
    items: list[Item],
    complete: set[str],
    now: datetime | None = None,
) -> int:
    """Upsert one connection's commitments; ones no longer listed are retired."""
    now = now or utcnow()
    existing = {
        (c.kind, c.commitment_id): c
        for c in db.scalars(select(Commitment).where(Commitment.connection_id == conn.id))
    }
    for it in items:
        row = existing.get((it.kind, it.commitment_id))
        if row is None:
            row = Commitment(
                org_id=conn.org_id,
                connection_id=conn.id,
                kind=it.kind,
                commitment_id=it.commitment_id[:300],
                alerts_sent=[],
            )
            db.add(row)
            existing[(it.kind, it.commitment_id)] = row
        project = it.data.get("project")
        row.account_id = str(project)[:32] if project else None
        row.region, row.description, row.count = it.region, it.description[:300], it.count
        row.hourly_commitment, row.upfront, row.recurring_hourly = it.hourly_commitment, None, None
        row.currency, row.term_years, row.payment_option = (
            it.currency[:3],
            it.term_years,
            it.payment_option,
        )
        row.offering_class, row.starts_at, row.ends_at = None, it.starts_at, it.ends_at
        row.state, row.data, row.seen_at = it.state, it.data, now
        row.utilization_pct = it.data.get("utilization")
    seen = {(it.kind, it.commitment_id) for it in items}
    for key, row in existing.items():
        if key[0] in complete and key not in seen:
            row.state = "retired"
    db.flush()
    return len(items)


def collect(db: Session, client: Any, connection_id: uuid.UUID) -> dict[str, Any]:
    """Collect commitments for one Azure or Google Cloud connection."""
    set_tenant(db, bypass=True)
    conn = db.get(CloudConnection, connection_id)
    if conn is None or conn.provider not in ("azure", "gcp"):
        return {}
    set_tenant(db, org_id=conn.org_id)
    if conn.provider == "azure":
        items, errors, complete = azure_items(client)
    else:
        from ccradar.clouds.gcp.recommender import locations

        projects = sorted({p for p, _, _ in locations(db, conn.org_id)})
        items, errors, complete = gcp_items(client, projects)
    return {"count": store(db, conn, items, complete), "errors": errors}
