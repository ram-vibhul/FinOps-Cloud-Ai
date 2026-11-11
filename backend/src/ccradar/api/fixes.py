"""Opt-in automatic fixes: turn on per AWS connection, request, approve, run, list."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from ccradar.accounts.roles import Permission
from ccradar.api.deps import SessionDep, SettingsDep, plan_feature, require
from ccradar.auth.web import AuthContext
from ccradar.clouds.aws import fixes
from ccradar.core.errors import AppError, NotFoundError
from ccradar.models import CloudConnection, Finding, FixAction, User

router = APIRouter(tags=["automatic fixes"])
Reader = Annotated[AuthContext, require(Permission.READ)]
Writer = Annotated[AuthContext, require(Permission.WRITE)]
Admin = Annotated[AuthContext, require(Permission.MANAGE_SETTINGS)]
Owner = Annotated[AuthContext, require(Permission.MANAGE_BILLING)]


class EnableIn(BaseModel):
    role_arn: str = Field(pattern=r"^arn:aws:iam::\d{12}:role/[\w+=,.@/-]+$", max_length=600)
    days: int = Field(default=30, ge=1, le=fixes.MAX_DAYS)
    max_actions_per_day: int = Field(default=10, ge=1, le=500)
    spend_limit_monthly: float = Field(default=0, ge=0, le=10_000_000)
    auto_request: bool = False  # 8.11: CloudCostRadar files requests for week-old idle resources
    confirm: bool = False


def _conn(db: SessionDep, connection_id: uuid.UUID) -> CloudConnection:
    c = db.get(CloudConnection, connection_id)
    if c is None:
        raise NotFoundError("Connection not found.")
    return c


def _out(db: SessionDep, x: FixAction) -> dict[str, Any]:
    def email(uid: uuid.UUID | None) -> str | None:
        u = db.get(User, uid) if uid else None
        return u.email if u else None

    f = db.get(Finding, x.finding_id) if x.finding_id else None
    return {
        "id": str(x.id),
        "finding_id": str(x.finding_id) if x.finding_id else None,
        "finding": f.title if f else None,
        "action": x.action,
        "label": fixes.LABELS[x.action],
        "resource_id": x.resource_id,
        "region": x.region,
        "savings_monthly": x.savings_monthly,
        "status": x.status,
        "requested_by": email(x.requested_by_id)
        if x.requested_by_id
        else "CloudCostRadar (auto clean-up)",
        "approved_by": email(x.approved_by_id),
        "executed_at": x.executed_at.isoformat() if x.executed_at else None,
        "result": x.result or {},
        "created_at": x.created_at.isoformat(),
    }


@router.get("/api/v1/connections/{connection_id}/fixes")
def fixes_status(
    connection_id: uuid.UUID, auth: Reader, db: SessionDep, settings: SettingsDep
) -> dict[str, Any]:
    conn = _conn(db, connection_id)
    cfg = fixes.settings_of(conn)
    out: dict[str, Any] = {
        "enabled": cfg is not None,
        "settings": cfg,
        "role_name": fixes.ROLE_NAME,
        "protect_tag": fixes.PROTECT_TAG,
    }
    if settings.aws_principal_arn:
        out["cloudformation"] = fixes.cloudformation(settings.aws_principal_arn, conn.external_id)
    return out


@router.post("/api/v1/connections/{connection_id}/fixes", dependencies=[plan_feature("auto_fixes")])
def enable_fixes(
    connection_id: uuid.UUID, body: EnableIn, auth: Owner, db: SessionDep, request: Request
) -> dict[str, Any]:
    """Owners only, with an explicit confirmation: this lets CloudCostRadar change resources."""
    if not body.confirm:
        raise AppError(
            "Confirm that CloudCostRadar may change resources in this account.",
            code="confirm_required",
        )
    cfg = fixes.enable(
        db,
        request.app.state.aws,
        _conn(db, connection_id),
        role_arn=body.role_arn,
        days=body.days,
        max_actions_per_day=body.max_actions_per_day,
        spend_limit_monthly=body.spend_limit_monthly,
        user_id=auth.user.id,
        auto_request=body.auto_request,
    )
    return {"enabled": True, "settings": cfg}


@router.delete("/api/v1/connections/{connection_id}/fixes", status_code=204)
def disable_fixes(connection_id: uuid.UUID, auth: Admin, db: SessionDep) -> Response:
    """Any admin can turn automatic fixes off at once."""
    fixes.disable(db, _conn(db, connection_id))
    return Response(status_code=204)


@router.post(
    "/api/v1/findings/{finding_id}/fix", status_code=201, dependencies=[plan_feature("auto_fixes")]
)
def request_fix(finding_id: uuid.UUID, auth: Writer, db: SessionDep) -> dict[str, Any]:
    f = db.get(Finding, finding_id)
    if f is None:
        raise NotFoundError("Finding not found.")
    return _out(db, fixes.request(db, f, auth.user.id))


@router.post(
    "/api/v1/purchases/{purchase_id}/buy",
    status_code=201,
    dependencies=[plan_feature("auto_fixes")],
)
def request_purchase(purchase_id: uuid.UUID, auth: Writer, db: SessionDep) -> dict[str, Any]:
    """Ask CloudCostRadar to buy an approved Savings Plan (needs a second person's approval)."""
    from ccradar.models import PlannedPurchase

    p = db.get(PlannedPurchase, purchase_id)
    if p is None:
        raise NotFoundError("Purchase not found.")
    return _out(db, fixes.request_purchase(db, p, auth.user.id))


@router.get("/api/v1/fixes")
def list_fixes(auth: Reader, db: SessionDep) -> list[dict[str, Any]]:
    return [
        _out(db, x)
        for x in db.scalars(select(FixAction).order_by(FixAction.created_at.desc()).limit(200))
    ]


@router.post("/api/v1/fixes/{fix_id}/approve", dependencies=[plan_feature("auto_fixes")])
def approve_fix(fix_id: uuid.UUID, auth: Admin, db: SessionDep) -> dict[str, Any]:
    """Approved fixes run within a minute (job ``fixes.run_approved``)."""
    x = db.get(FixAction, fix_id)
    if x is None:
        raise NotFoundError("Fix not found.")
    return _out(db, fixes.approve(db, x, auth.user.id))


@router.post("/api/v1/fixes/{fix_id}/reject")
def reject_fix(fix_id: uuid.UUID, auth: Writer, db: SessionDep) -> dict[str, Any]:
    x = db.get(FixAction, fix_id)
    if x is None:
        raise NotFoundError("Fix not found.")
    return _out(db, fixes.reject(db, x, auth.user.id))


@router.get("/api/v1/org/write-access")
def write_access(auth: Reader, db: SessionDep) -> list[dict[str, Any]]:
    """Accounts where CloudCostRadar may currently change resources (for the banner on every page)."""
    out = []
    for c in db.scalars(select(CloudConnection).where(CloudConnection.provider == "aws")):
        cfg = fixes.settings_of(c)
        if cfg is None:
            continue
        who = db.get(User, uuid.UUID(cfg["enabled_by"])) if cfg.get("enabled_by") else None
        out.append(
            {
                "connection_id": str(c.id),
                "name": c.name,
                "account_id": c.account_id,
                "enabled_by": who.email if who else None,
                "enabled_at": cfg.get("enabled_at"),
                "expires_at": cfg.get("expires_at"),
                "auto_request": bool(cfg.get("auto_request")),
            }
        )
    return out
