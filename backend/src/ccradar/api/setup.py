"""Guided setup (9.1): the next steps for a new organisation, worked out from what it has
actually done (nothing to tick by hand), and a way to hide the checklist when done."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Response
from sqlalchemy import func, select

from ccradar.accounts.roles import Permission
from ccradar.api.deps import SessionDep, require
from ccradar.auth.web import AuthContext
from ccradar.models import (
    Budget,
    CloudConnection,
    CostDaily,
    Finding,
    K8sCluster,
    Membership,
    NotificationChannel,
    Organization,
)

router = APIRouter(prefix="/api/v1/org/setup", tags=["setup"])
Reader = Annotated[AuthContext, require(Permission.READ)]
Admin = Annotated[AuthContext, require(Permission.MANAGE_SETTINGS)]


def _count(db: SessionDep, stmt: Any) -> int:
    return int(db.scalar(stmt) or 0)


def steps(db: SessionDep) -> list[dict[str, Any]]:
    clouds = _count(
        db,
        select(func.count()).select_from(CloudConnection).where(CloudConnection.status == "active"),
    )
    costs = _count(db, select(func.count()).select_from(CostDaily))
    members = _count(db, select(func.count()).select_from(Membership))
    channels = _count(
        db,
        select(func.count())
        .select_from(NotificationChannel)
        .where(NotificationChannel.enabled.is_(True)),
    )
    budgets = _count(db, select(func.count()).select_from(Budget))
    acted = _count(db, select(func.count()).select_from(Finding).where(Finding.status != "open"))
    clusters = _count(db, select(func.count()).select_from(K8sCluster))
    return [
        {
            "key": "connect_cloud",
            "title": "Connect a cloud account",
            "why": "CloudCostRadar reads costs and resources through a read-only role.",
            "done": clouds > 0,
            "link": "/settings?tab=clouds",
        },
        {
            "key": "first_costs",
            "title": "See your first costs",
            "why": "The first import takes a few minutes after connecting.",
            "done": costs > 0,
            "link": "/costs",
        },
        {
            "key": "review_savings",
            "title": "Act on a saving",
            "why": "Approve, apply or dismiss one of the savings CloudCostRadar found.",
            "done": acted > 0,
            "link": "/savings",
        },
        {
            "key": "budget",
            "title": "Set a budget",
            "why": "Get warned before a month runs over.",
            "done": budgets > 0,
            "link": "/governance?tab=budgets",
        },
        {
            "key": "alerts",
            "title": "Send alerts to Slack or Teams",
            "why": "Unusual increases, budgets and early warnings where your team already is.",
            "done": channels > 0,
            "link": "/settings?tab=notifications",
        },
        {
            "key": "invite",
            "title": "Invite your team",
            "why": "Engineers fix waste fastest when they see it.",
            "done": members > 1,
            "link": "/settings?tab=members",
        },
        {
            "key": "kubernetes",
            "title": "Connect a Kubernetes cluster (optional)",
            "why": "Costs per namespace and workload, right-sizing and incident help.",
            "done": clusters > 0,
            "link": "/settings?tab=kubernetes",
            "optional": True,
        },
    ]


@router.get("")
def checklist(auth: Reader, db: SessionDep) -> dict[str, Any]:
    org = db.get(Organization, auth.org_id)
    items = steps(db)
    required = [s for s in items if not s.get("optional")]
    nxt = next((s["key"] for s in required if not s["done"]), None)
    return {
        "steps": items,
        "done": sum(1 for s in required if s["done"]),
        "total": len(required),
        "next": nxt,
        "hidden": bool(((org.settings if org else None) or {}).get("setup_hidden")),
    }


@router.post("/hide", status_code=204)
def hide(auth: Admin, db: SessionDep) -> Response:
    org = db.get(Organization, auth.org_id)
    assert org is not None  # noqa: S101
    org.settings = {**(org.settings or {}), "setup_hidden": True}
    db.flush()
    return Response(status_code=204)


@router.get("/first-savings")
def first_savings(auth: Reader, db: SessionDep) -> dict[str, Any]:
    """Where onboarding stands and the biggest savings found so far (9.5)."""
    from ccradar.findings.first_savings import summary

    org = db.get(Organization, auth.org_id)
    assert org is not None  # noqa: S101 - guaranteed by require()
    return summary(db, org, auth.scope)
