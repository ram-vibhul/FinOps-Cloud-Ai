"""Privacy rights: personal export, account deletion, organisation export and deletion."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from costlens.accounts import service as acc
from costlens.accounts.roles import Permission
from costlens.api.deps import CurrentAuth, SessionDep, SettingsDep, require
from costlens.auth.web import AuthContext, clear_session_cookie
from costlens.core.errors import AppError, NotFoundError
from costlens.db.tenant import set_tenant
from costlens.models import DataExport, Organization
from costlens.privacy import retention, service

router = APIRouter(tags=["privacy"])
Owner = Annotated[AuthContext, require(Permission.DELETE_ORGANISATION)]
Admin = Annotated[AuthContext, require(Permission.MANAGE_SETTINGS)]
Reader = Annotated[AuthContext, require(Permission.READ)]


class ConfirmEmail(BaseModel):
    confirm_email: str = Field(max_length=320)


class ConfirmSlug(BaseModel):
    confirm_slug: str = Field(max_length=63)


class ExportOut(BaseModel):
    id: uuid.UUID
    status: str
    size_bytes: int | None
    created_at: datetime
    expires_at: datetime | None
    download_url: str | None


class DeletionOut(BaseModel):
    purge_after: datetime


def _export_out(e: DataExport) -> ExportOut:
    url = f"/api/v1/org/exports/{e.id}/download" if service.export_available(e) else None
    return ExportOut(
        id=e.id,
        status=e.status,
        size_bytes=e.size_bytes,
        created_at=e.created_at,
        expires_at=e.expires_at,
        download_url=url,
    )


@router.get("/api/v1/me/export")
def export_me(auth: CurrentAuth, db: SessionDep) -> Response:
    """Everything CostLens stores about you, as a JSON file."""
    body = json.dumps(service.export_user(db, auth.user), indent=2)
    return Response(
        body,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="costlens-my-data.json"'},
    )


@router.delete("/api/v1/me", status_code=204)
def delete_me(
    body: ConfirmEmail, auth: CurrentAuth, db: SessionDep, settings: SettingsDep
) -> Response:
    """Delete your account. Type your email address to confirm."""
    if body.confirm_email.strip().lower() != auth.user.email:
        raise AppError("Type your email address exactly to confirm.", code="confirmation_mismatch")
    service.delete_user(db, auth.user)
    response = Response(status_code=204)
    clear_session_cookie(response, settings)
    return response


@router.post("/api/v1/org/exports", response_model=ExportOut, status_code=202)
def request_export(auth: Owner, db: SessionDep, request: Request) -> ExportOut:
    assert auth.org_id is not None  # noqa: S101
    export = service.request_org_export(db, auth.org_id, auth.user.id)
    db.commit()  # the job (possibly another process) must see the row
    request.app.state.jobs.enqueue("privacy.build_export", export_id=str(export.id))
    set_tenant(db, org_id=auth.org_id, user_id=auth.user.id)
    db.refresh(export)
    return _export_out(export)


@router.get("/api/v1/org/exports", response_model=list[ExportOut])
def list_exports(auth: Owner, db: SessionDep) -> list[ExportOut]:
    rows = db.scalars(select(DataExport).order_by(DataExport.created_at.desc()).limit(20))
    return [_export_out(e) for e in rows]


@router.get("/api/v1/org/exports/{export_id}/download")
def download_export(
    export_id: uuid.UUID, auth: Owner, db: SessionDep, request: Request
) -> Response:
    export = db.get(DataExport, export_id)
    if export is None or not service.export_available(export) or export.storage_key is None:
        raise NotFoundError("This export is not available (it may have expired).")
    data = request.app.state.storage.get(export.storage_key)
    return Response(
        data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="costlens-export-{export.id}.zip"'},
    )


@router.delete("/api/v1/org", response_model=DeletionOut, status_code=202)
def delete_org(body: ConfirmSlug, auth: Owner, db: SessionDep) -> DeletionOut:
    """Schedule the organisation for deletion. Type its address (slug) to confirm."""
    org = db.get(Organization, auth.org_id)
    if org is None:
        raise NotFoundError("Organisation not found.")
    if body.confirm_slug.strip().lower() != org.slug:
        raise AppError(
            "Type the organisation address exactly to confirm.", code="confirmation_mismatch"
        )
    purge_after = service.request_org_deletion(db, org)
    auth.session.active_org_id = None
    db.flush()
    return DeletionOut(purge_after=purge_after)


@router.post("/api/v1/organizations/{org_id}/restore", status_code=204)
def restore(org_id: uuid.UUID, auth: CurrentAuth, db: SessionDep) -> Response:
    """Cancel a pending deletion (owners, during the grace period)."""
    set_tenant(db, org_id=org_id, user_id=auth.user.id)
    acc.require_permission(db, org_id, auth.user.id, Permission.DELETE_ORGANISATION)
    org = db.get(Organization, org_id)
    if org is None:
        raise NotFoundError("Organisation not found.")
    service.restore_org(db, org)
    acc.switch_organization(db, auth.session, org_id)
    return Response(status_code=204)


class RetentionOut(BaseModel):
    kind: str
    label: str
    description: str
    days: int
    default_days: int
    min_days: int
    max_days: int


@router.get("/api/v1/org/retention", response_model=list[RetentionOut])
def get_retention(auth: Reader, db: SessionDep) -> list[RetentionOut]:
    org = db.get(Organization, auth.org_id)
    assert org is not None  # noqa: S101
    return [
        RetentionOut(
            kind=p.kind,
            label=p.label,
            description=p.description,
            days=retention.effective_days(org, p),
            default_days=p.default_days,
            min_days=p.min_days,
            max_days=p.max_days,
        )
        for p in retention.policies()
    ]


@router.patch("/api/v1/org/retention", response_model=list[RetentionOut])
def update_retention(
    body: dict[str, int], auth: Admin, db: SessionDep, settings: SettingsDep
) -> list[RetentionOut]:
    """Set days to keep per kind of data, e.g. {"audit_log": 730}."""
    from costlens.billing.plans import plan_for

    org = db.get(Organization, auth.org_id)
    assert org is not None  # noqa: S101
    plan = plan_for(org, settings)
    longest = plan.limit("retention_days")
    if longest is not None and any(days > longest for days in body.values()):
        raise AppError(
            f"The {plan.name} plan keeps data for at most {longest:,} days.",
            status=402,
            code="plan_limit",
        )
    retention.set_retention(org, body)
    db.flush()
    return get_retention(auth, db)
