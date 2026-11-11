"""The organisation's subscription (B.2): see it, change plan, cancel, resume."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ccradar.accounts.roles import Permission
from ccradar.api.deps import SessionDep, SettingsDep, require
from ccradar.auth.web import AuthContext
from ccradar.billing import plans, subscriptions
from ccradar.core.errors import AppError, NotFoundError
from ccradar.models import Organization, Subscription

router = APIRouter(prefix="/api/v1/org/subscription", tags=["subscription"])
Reader = Annotated[AuthContext, require(Permission.READ)]
Billing = Annotated[AuthContext, require(Permission.MANAGE_BILLING)]


def _org(db: SessionDep, auth: AuthContext) -> Organization:
    org = db.get(Organization, auth.org_id)
    if org is None:
        raise NotFoundError("Organisation not found.")
    return org


def _out(sub: Subscription | None, org: Organization, settings: SettingsDep) -> dict[str, Any]:
    plan = plans.plan_for(org, settings)
    cat = plans.catalogue(settings)
    base = {
        "plan": {"id": plan.id, "name": plan.name},
        "payments_available": False,  # until Razorpay / Stripe (B.3, B.4)
        "choices": [{"id": p.id, "name": p.name} for p in cat.plans.values()],
    }
    if sub is None:
        return {**base, "status": "none"}
    pending = cat.get(sub.pending_plan_id) if sub.pending_plan_id else None
    return {
        **base,
        "status": sub.status,
        "interval": sub.interval,
        "provider": sub.provider,
        "trial_ends_at": sub.trial_ends_at.isoformat() if sub.trial_ends_at else None,
        "current_period_end": sub.current_period_end.isoformat(),
        "cancel_at_period_end": sub.cancel_at_period_end,
        "pending_plan": {"id": pending.id, "name": pending.name} if pending else None,
    }


@router.get("")
def get_subscription(auth: Reader, db: SessionDep, settings: SettingsDep) -> dict[str, Any]:
    org = _org(db, auth)
    return _out(subscriptions.get(db, org.id), org, settings)


class ChangeIn(BaseModel):
    plan: str = Field(max_length=50)
    interval: str = Field(default="monthly", pattern="^(monthly|annual)$")


@router.post("/change")
def change_plan(
    body: ChangeIn, auth: Billing, db: SessionDep, settings: SettingsDep
) -> dict[str, Any]:
    """Downgrades are scheduled for the end of the period; upgrades need a payment."""
    org = _org(db, auth)
    result = subscriptions.change(db, org, plans.catalogue(settings), body.plan, body.interval)
    return {**result, "subscription": _out(subscriptions.get(db, org.id), org, settings)}


def _existing(db: SessionDep, org: Organization) -> Subscription:
    sub = subscriptions.get(db, org.id)
    if sub is None:
        raise AppError("There is no subscription to change.", status=409, code="no_subscription")
    return sub


@router.post("/cancel")
def cancel(auth: Billing, db: SessionDep, settings: SettingsDep) -> dict[str, Any]:
    """Keeps the plan until the end of the paid period (a trial ends at once)."""
    org = _org(db, auth)
    sub = _existing(db, org)
    result = subscriptions.cancel(sub)
    db.flush()
    return {**result, "subscription": _out(sub, org, settings)}


@router.post("/resume")
def resume(auth: Billing, db: SessionDep, settings: SettingsDep) -> dict[str, Any]:
    org = _org(db, auth)
    sub = _existing(db, org)
    subscriptions.resume(sub)
    db.flush()
    return _out(sub, org, settings)
