"""Plans and what they include (B.1): the plan catalogue, the organisation's plan with its
usage against each limit, and a staff-only switch until subscriptions (B.2) set plans."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from costlens.accounts.roles import Permission
from costlens.api.deps import CurrentAuth, SessionDep, SettingsDep, require
from costlens.auth.web import AuthContext
from costlens.billing import plans
from costlens.billing.usage import current_usage
from costlens.config import DeploymentMode
from costlens.core.errors import AppError, NotFoundError, PermissionDeniedError
from costlens.db.tenant import set_tenant
from costlens.models import Organization

router = APIRouter(tags=["plans"])


def db_of(org: Organization) -> Any:
    from sqlalchemy.orm import object_session

    return object_session(org)


Reader = Annotated[AuthContext, require(Permission.READ)]


def _plan_out(p: plans.Plan) -> dict[str, Any]:
    return {
        "id": p.id,
        "name": p.name,
        "features": sorted(p.features),
        "limits": p.limits,
    }


@router.get("/api/v1/plans")
def catalogue(settings: SettingsDep) -> dict[str, Any]:
    """The plans on offer (features and limits; prices come with the payment providers)."""
    cat = plans.catalogue(settings)
    return {
        "placeholder": cat.placeholder,
        "features": plans.FEATURES,
        "limits": plans.LIMITS,
        "plans": [_plan_out(p) for p in cat.plans.values()],
    }


def _source(org: Organization, settings: SettingsDep, plan: plans.Plan) -> str:
    from costlens.billing.subscriptions import get

    if plan is plans.UNLIMITED:
        return "demo" if org.is_demo else "local"
    sub = get(db_of(org), org.id)
    if sub is not None and sub.status == "trialing":
        return "trial"
    if (sub is not None and sub.status != "ended") or (org.settings or {}).get("plan") == plan.id:
        return "chosen"
    if settings.deployment_mode is DeploymentMode.SELF_HOSTED:
        return "free_tier"
    cat = plans.catalogue(settings)
    return "trial" if plan.id == cat.trial_plan and cat.default_plan != plan.id else "default"


@router.get("/api/v1/org/plan")
def org_plan(auth: Reader, db: SessionDep, settings: SettingsDep) -> dict[str, Any]:
    """This organisation's plan, what it includes, and usage against each limit."""
    org = db.get(Organization, auth.org_id)
    if org is None:
        raise NotFoundError("Organisation not found.")
    plan = plans.plan_for(org, settings)
    usage = current_usage(db)
    return {
        "plan": {"id": plan.id, "name": plan.name},
        "source": _source(org, settings, plan),
        "placeholder": plans.catalogue(settings).placeholder,
        "features": [
            {"id": f, "label": label, "included": plan.allows(f)}
            for f, label in plans.FEATURES.items()
        ],
        "limits": [
            {"key": k, "label": label, "limit": plan.limit(k), "used": usage.get(k)}
            for k, label in plans.LIMITS.items()
        ],
    }


class PlanIn(BaseModel):
    plan: str | None = Field(default=None, max_length=50)  # None: end it (default plan)
    interval: str = Field(default="annual", pattern="^(monthly|annual)$")


@router.put("/api/v1/platform/organizations/{org_id}/plan")
def set_plan(
    org_id: uuid.UUID, body: PlanIn, auth: CurrentAuth, db: SessionDep, settings: SettingsDep
) -> dict[str, Any]:
    """CostLens staff only: record an invoiced (manual) subscription, or end one."""
    from costlens.billing import subscriptions

    if not auth.user.is_platform_admin:
        raise PermissionDeniedError("Only CostLens staff can do this.")
    if body.plan is not None and plans.catalogue(settings).get(body.plan) is None:
        raise AppError(f"Unknown plan {body.plan!r}.", status=422, code="unknown_plan")
    set_tenant(db, bypass=True)
    org = db.get(Organization, org_id)
    if org is None:
        raise NotFoundError("Organisation not found.")
    org.settings = {k: v for k, v in (org.settings or {}).items() if k != "plan"}
    if body.plan is not None:
        subscriptions.activate(db, org, body.plan, body.interval)
    elif (sub := subscriptions.get(db, org.id)) is not None:
        subscriptions.end(sub)
    db.flush()
    plan = plans.plan_for(org, settings)
    set_tenant(db, org_id=auth.org_id, user_id=auth.user.id)
    return {"id": str(org.id), "plan": plan.id}


@router.get("/api/v1/system/licence")
def licence_status(auth: CurrentAuth, db: SessionDep, settings: SettingsDep) -> dict[str, Any]:
    """The installation's licence (self-hosted)."""
    from costlens.billing import licence

    out = licence.status(db, settings)
    return {"deployment_mode": settings.deployment_mode.value, **out}


class LicenceIn(BaseModel):
    key: str = Field(min_length=10, max_length=8000)


@router.put("/api/v1/system/licence")
def install_licence(
    body: LicenceIn, auth: CurrentAuth, db: SessionDep, settings: SettingsDep
) -> dict[str, Any]:
    """Platform admins: install a licence key (checked before it is saved)."""
    from costlens.billing import licence

    if not auth.user.is_platform_admin:
        raise PermissionDeniedError("Only an administrator of this installation can do this.")
    try:
        licence.install(db, body.key)
    except licence.LicenceError as exc:
        raise AppError(str(exc), status=422, code="bad_licence") from exc
    return licence_status(auth, db, settings)


@router.get("/api/v1/org/usage")
def org_usage(
    auth: Reader, db: SessionDep, settings: SettingsDep, days: int = 30
) -> dict[str, Any]:
    """Usage now and per day for the last ``days`` days (B.12)."""
    from costlens.billing.metering import history

    days = max(1, min(days, 400))
    return {"now": current_usage(db), "days": history(db, days)}
