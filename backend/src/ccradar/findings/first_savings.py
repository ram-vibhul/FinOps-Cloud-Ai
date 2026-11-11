"""The first savings of a new organisation (9.5): where onboarding stands, and the
biggest savings found so far, so a new customer sees value within minutes."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ccradar.accounts.custom_roles import finding_visible
from ccradar.db.base import utcnow
from ccradar.models import CloudConnection, CostDaily, Finding, Organization

TOP = 3


def mark(org: Organization, status: str, **extra: Any) -> None:
    """Record the first-run state in the organisation's settings."""
    first = {**(org.settings or {}).get("first_run", {}), "status": status, **extra}
    first[f"{status}_at"] = utcnow().isoformat()
    org.settings = {**(org.settings or {}), "first_run": first}


def summary(
    db: Session, org: Organization, scope: dict[str, list[str]] | None = None
) -> dict[str, Any]:
    """``stage``: connect → importing → analysing → ready (or none_found)."""
    open_findings = [
        f
        for f in db.scalars(
            select(Finding).where(Finding.status == "open", Finding.savings_monthly > 0)
        )
        if finding_visible(scope, f)
    ]
    best: dict[str, Finding] = {}
    for f in open_findings:  # the same resource is never counted twice
        if f.overlap_key not in best or f.savings_monthly > best[f.overlap_key].savings_monthly:
            best[f.overlap_key] = f
    ranked = sorted(best.values(), key=lambda f: f.savings_monthly, reverse=True)
    currency = org.default_currency or "USD"
    total = float(sum(f.savings_monthly for f in ranked if f.currency == currency))
    first = (org.settings or {}).get("first_run") or {}
    connected = db.scalar(
        select(func.count()).select_from(CloudConnection).where(CloudConnection.status == "active")
    )
    has_costs = db.scalar(select(CostDaily.org_id).limit(1)) is not None
    if ranked:
        stage = "ready"
    elif not connected:
        stage = "connect"
    elif first.get("status") == "running":
        stage = "analysing" if has_costs else "importing"
    elif first.get("status") == "done" or (has_costs and first):
        stage = "none_found"
    else:
        stage = "analysing" if has_costs else "importing"
    return {
        "stage": stage,
        "count": len(ranked),
        "savings_monthly": round(total, 2),
        "currency": currency,
        "top": [
            {
                "id": str(f.id),
                "title": f.title,
                "savings_monthly": float(f.savings_monthly),
                "currency": f.currency,
            }
            for f in ranked[:TOP]
        ],
        "started_at": first.get("running_at"),
        "finished_at": first.get("done_at"),
    }
