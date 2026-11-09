"""Temporary elevated access: request, approve, grant, deny, revoke, list."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from costlens.accounts import elevation
from costlens.accounts.roles import Permission, Role, has_permission
from costlens.api.deps import SessionDep, require
from costlens.auth.web import AuthContext
from costlens.core.errors import NotFoundError
from costlens.core.notify import Message, notify
from costlens.models import ElevatedAccess, Membership, User

router = APIRouter(prefix="/api/v1/org/elevated-access", tags=["elevated access"])
Reader = Annotated[AuthContext, require(Permission.READ)]
Admin = Annotated[AuthContext, require(Permission.MANAGE_MEMBERS)]


class RequestIn(BaseModel):
    role: Role
    minutes: int = Field(ge=15, le=elevation.MAX_MINUTES)
    reason: str = Field(min_length=5, max_length=500)


class GrantIn(RequestIn):
    user_id: uuid.UUID


def _out(db: SessionDep, g: ElevatedAccess) -> dict[str, Any]:
    who = db.get(User, g.user_id)
    by = db.get(User, g.decided_by_id) if g.decided_by_id else None
    return {
        "id": str(g.id),
        "user_id": str(g.user_id),
        "user": who.email if who else None,
        "role": g.role,
        "minutes": g.minutes,
        "reason": g.reason,
        "status": g.status,
        "decided_by": by.email if by else None,
        "starts_at": g.starts_at.isoformat() if g.starts_at else None,
        "expires_at": g.expires_at.isoformat() if g.expires_at else None,
        "created_at": g.created_at.isoformat(),
    }


def _member_role(db: SessionDep, auth: AuthContext) -> Role:
    """Decisions use the decider's own membership role, never an elevated one."""
    m = db.scalar(
        select(Membership).where(
            Membership.org_id == auth.org_id, Membership.user_id == auth.user.id
        )
    )
    return m.role if m else Role.VIEWER


def _get(db: SessionDep, gid: uuid.UUID) -> ElevatedAccess:
    g = db.get(ElevatedAccess, gid)
    if g is None:
        raise NotFoundError("Request not found.")
    return g


@router.get("")
def list_access(auth: Reader, db: SessionDep) -> list[dict[str, Any]]:
    elevation.expire(db)
    q = select(ElevatedAccess).order_by(ElevatedAccess.created_at.desc()).limit(100)
    if not has_permission(_member_role(db, auth), Permission.MANAGE_MEMBERS):
        q = q.where(ElevatedAccess.user_id == auth.user.id)
    return [_out(db, g) for g in db.scalars(q)]


@router.post("", status_code=201)
def request_access(body: RequestIn, auth: Reader, db: SessionDep) -> dict[str, Any]:
    assert auth.org_id is not None  # noqa: S101
    g = elevation.request(db, auth.org_id, auth.user.id, body.role, body.minutes, body.reason)
    notify(
        db,
        "access_request",
        [
            Message(
                title=f"{auth.user.email} asks for {body.role.value} access for {body.minutes} minutes",
                text=body.reason,
                link="/settings?tab=members",
                severity="info",
            )
        ],
    )
    return _out(db, g)


@router.post("/grant", status_code=201)
def grant_access(body: GrantIn, auth: Admin, db: SessionDep) -> dict[str, Any]:
    assert auth.org_id is not None  # noqa: S101
    g = elevation.grant(
        db,
        auth.org_id,
        auth.user.id,
        _member_role(db, auth),
        body.user_id,
        body.role,
        body.minutes,
        body.reason,
    )
    return _out(db, g)


@router.post("/{gid}/approve")
def approve(gid: uuid.UUID, auth: Admin, db: SessionDep) -> dict[str, Any]:
    return _out(db, elevation.approve(db, _get(db, gid), auth.user.id, _member_role(db, auth)))


@router.post("/{gid}/deny")
def deny(gid: uuid.UUID, auth: Admin, db: SessionDep) -> dict[str, Any]:
    return _out(db, elevation.deny(db, _get(db, gid), auth.user.id, _member_role(db, auth)))


@router.post("/{gid}/revoke")
def revoke(gid: uuid.UUID, auth: Reader, db: SessionDep) -> dict[str, Any]:
    return _out(db, elevation.revoke(db, _get(db, gid), auth.user.id, _member_role(db, auth)))
