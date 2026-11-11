"""Organisations: create, switch, settings, members, ownership transfer."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from ccradar.accounts import service as acc
from ccradar.accounts.roles import Permission, Role
from ccradar.api.deps import CurrentAuth, SessionDep, SettingsDep, require
from ccradar.auth.web import AuthContext
from ccradar.core.errors import AppError, NotFoundError, PermissionDeniedError
from ccradar.models import Membership, Organization, User

router = APIRouter(tags=["organizations"])
Reader = Annotated[AuthContext, require(Permission.READ)]
Settings_ = Annotated[AuthContext, require(Permission.MANAGE_SETTINGS)]
Owner = Annotated[AuthContext, require(Permission.MANAGE_OWNERS)]

CURRENCY = r"^[A-Z]{3}$"


class OrgIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class OrgPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    default_currency: str | None = Field(default=None, pattern=CURRENCY)


class OrgOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    is_demo: bool
    default_currency: str
    created_at: datetime


class SwitchIn(BaseModel):
    organization_id: uuid.UUID


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    name: str
    avatar_url: str | None
    role: Role
    joined_at: datetime


class RoleIn(BaseModel):
    role: Role


class TransferIn(BaseModel):
    user_id: uuid.UUID


def _org_out(org: Organization) -> OrgOut:
    return OrgOut.model_validate(org, from_attributes=True)


def _current_org(db: SessionDep, auth: AuthContext) -> Organization:
    org = db.get(Organization, auth.org_id)
    if org is None:
        raise NotFoundError("Organisation not found.")
    return org


@router.post("/api/v1/organizations", response_model=OrgOut, status_code=201)
def create_organization(
    body: OrgIn, auth: CurrentAuth, db: SessionDep, settings: SettingsDep
) -> OrgOut:
    """Create an organisation (you become its owner) and switch to it."""
    from ccradar.accounts.trial import start_trial

    org = acc.create_organization(db, name=body.name, owner=auth.user)
    start_trial(db, org, settings)
    acc.switch_organization(db, auth.session, org.id)
    return _org_out(org)


@router.post("/api/v1/auth/switch", status_code=204)
def switch(body: SwitchIn, auth: CurrentAuth, db: SessionDep) -> Response:
    acc.switch_organization(db, auth.session, body.organization_id)
    return Response(status_code=204)


@router.get("/api/v1/org", response_model=OrgOut)
def get_org(auth: Reader, db: SessionDep) -> OrgOut:
    return _org_out(_current_org(db, auth))


@router.patch("/api/v1/org", response_model=OrgOut)
def update_org(body: OrgPatch, auth: Settings_, db: SessionDep) -> OrgOut:
    org = _current_org(db, auth)
    if org.is_demo:
        raise AppError("The demo organisation cannot be changed.", status=403, code="demo")
    if body.name is not None:
        org.name = body.name.strip()
    if body.default_currency is not None:
        org.default_currency = body.default_currency
    db.flush()
    return _org_out(org)


@router.get("/api/v1/org/members", response_model=list[MemberOut])
def members(auth: Reader, db: SessionDep) -> list[MemberOut]:
    rows = db.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.org_id == auth.org_id)
        .order_by(User.email)
    ).all()
    return [
        MemberOut(
            user_id=u.id,
            email=u.email,
            name=u.name,
            avatar_url=u.avatar_url,
            role=m.role,
            joined_at=m.created_at,
        )
        for m, u in rows
    ]


@router.patch("/api/v1/org/members/{user_id}", response_model=MemberOut)
def change_role(user_id: uuid.UUID, body: RoleIn, auth: Reader, db: SessionDep) -> MemberOut:
    assert auth.org_id is not None  # noqa: S101
    m = acc.change_role(
        db, org_id=auth.org_id, actor_id=auth.user.id, user_id=user_id, role=body.role
    )
    u = db.get(User, user_id)
    assert u is not None  # noqa: S101
    return MemberOut(
        user_id=u.id,
        email=u.email,
        name=u.name,
        avatar_url=u.avatar_url,
        role=m.role,
        joined_at=m.created_at,
    )


@router.delete("/api/v1/org/members/{user_id}", status_code=204)
def remove_member(user_id: uuid.UUID, auth: Reader, db: SessionDep) -> Response:
    """Remove a member, or leave the organisation when it is your own user id."""
    assert auth.org_id is not None  # noqa: S101
    acc.remove_member(db, org_id=auth.org_id, actor_id=auth.user.id, user_id=user_id)
    if user_id == auth.user.id:
        auth.session.active_org_id = None
        db.flush()
    return Response(status_code=204)


@router.post("/api/v1/org/transfer-ownership", status_code=204)
def transfer_ownership(body: TransferIn, auth: Owner, db: SessionDep) -> Response:
    """Make another member owner; you stay on as admin."""
    assert auth.org_id is not None  # noqa: S101
    if body.user_id == auth.user.id:
        raise AppError("You already own this organisation.", code="same_user")
    if acc.get_membership(db, auth.org_id, body.user_id) is None:
        raise NotFoundError("Member not found.")
    acc.change_role(
        db, org_id=auth.org_id, actor_id=auth.user.id, user_id=body.user_id, role=Role.OWNER
    )
    acc.change_role(
        db, org_id=auth.org_id, actor_id=auth.user.id, user_id=auth.user.id, role=Role.ADMIN
    )
    return Response(status_code=204)


__all__ = ["PermissionDeniedError", "router"]


@router.get("/api/v1/org/trial")
def trial(auth: Reader, db: SessionDep) -> dict[str, object]:
    """Days left in the free trial (status "none" when the organisation has no trial)."""
    from ccradar.accounts.trial import trial_status

    return trial_status(_current_org(db, auth))
