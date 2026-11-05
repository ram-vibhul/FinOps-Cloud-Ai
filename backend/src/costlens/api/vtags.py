"""Virtual tags: define, preview, recompute."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel
from sqlalchemy import select

from costlens.accounts.roles import Permission
from costlens.api.deps import SessionDep, require
from costlens.auth.web import AuthContext
from costlens.core.errors import ConflictError, NotFoundError
from costlens.costs import vtags
from costlens.costs.analytics import latest_date
from costlens.db.base import utcnow
from costlens.models import VirtualTag

router = APIRouter(prefix="/api/v1/virtual-tags", tags=["virtual tags"])
Reader = Annotated[AuthContext, require(Permission.READ)]
Admin = Annotated[AuthContext, require(Permission.MANAGE_SETTINGS)]


class VirtualTagOut(vtags.Definition):
    id: uuid.UUID
    updated_at: datetime


def _out(row: VirtualTag) -> VirtualTagOut:
    return VirtualTagOut(
        id=row.id,
        key=row.key,
        description=row.description,
        default_value=row.default_value,
        override_existing=row.override_existing,
        rules=[vtags.Rule.model_validate(r) for r in row.rules],
        updated_at=row.updated_at,
    )


def _recompute(request: Request, org_id: Any) -> None:
    request.app.state.jobs.enqueue("costs.recompute_virtual_tags", org_id=str(org_id))


@router.get("", response_model=list[VirtualTagOut])
def list_vtags(auth: Reader, db: SessionDep) -> list[VirtualTagOut]:
    return [_out(r) for r in db.scalars(select(VirtualTag).order_by(VirtualTag.key))]


@router.post("", response_model=VirtualTagOut, status_code=201)
def create_vtag(
    body: vtags.Definition, auth: Admin, db: SessionDep, request: Request
) -> VirtualTagOut:
    if db.scalar(select(VirtualTag).where(VirtualTag.key == body.key)) is not None:
        raise ConflictError("A virtual tag with this key exists.", code="duplicate_key")
    row = VirtualTag(
        org_id=auth.org_id,
        key=body.key,
        description=body.description,
        default_value=body.default_value,
        override_existing=body.override_existing,
        rules=[r.model_dump() for r in body.rules],
    )
    db.add(row)
    db.flush()
    out = _out(row)
    db.commit()
    _recompute(request, auth.org_id)
    return out


@router.put("/{vtag_id}", response_model=VirtualTagOut)
def update_vtag(
    vtag_id: uuid.UUID, body: vtags.Definition, auth: Admin, db: SessionDep, request: Request
) -> VirtualTagOut:
    row = db.get(VirtualTag, vtag_id)
    if row is None:
        raise NotFoundError("Virtual tag not found.")
    row.key, row.description, row.default_value = body.key, body.description, body.default_value
    row.override_existing, row.rules = body.override_existing, [r.model_dump() for r in body.rules]
    db.flush()
    out = _out(row)
    db.commit()
    _recompute(request, auth.org_id)
    return out


@router.delete("/{vtag_id}", status_code=204)
def delete_vtag(vtag_id: uuid.UUID, auth: Admin, db: SessionDep, request: Request) -> Response:
    row = db.get(VirtualTag, vtag_id)
    if row is None:
        raise NotFoundError("Virtual tag not found.")
    db.delete(row)
    db.commit()
    _recompute(request, auth.org_id)
    return Response(status_code=204)


class PreviewIn(BaseModel):
    definition: vtags.Definition
    days: int = 30


@router.post("/preview")
def preview(body: PreviewIn, auth: Reader, db: SessionDep) -> dict[str, Any]:
    """What this definition would produce over the last N days (nothing is saved)."""
    assert auth.org_id is not None  # noqa: S101
    end = latest_date(db, auth.org_id) or utcnow().date()
    days = max(1, min(body.days, 90))
    return vtags.preview(
        db, auth.org_id, body.definition, start=end - timedelta(days=days - 1), end=end
    )
