"""Custom roles (8.8): permission sets limited to accounts/teams, assigned to members."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from ccradar.accounts.custom_roles import GRANTABLE
from ccradar.accounts.roles import Permission, Role
from ccradar.api.deps import SessionDep, plan_feature, require
from ccradar.auth.web import AuthContext
from ccradar.core.errors import AppError, NotFoundError
from ccradar.models import CustomRole, Membership

router = APIRouter(prefix="/api/v1/org", tags=["custom roles"])
Admin = Annotated[AuthContext, require(Permission.MANAGE_MEMBERS)]
Reader = Annotated[AuthContext, require(Permission.READ)]


class RoleIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    permissions: list[str] = Field(default_factory=lambda: ["read"])
    accounts: list[str] = Field(default_factory=list, max_length=500)
    teams: list[str] = Field(default_factory=list, max_length=500)

    @field_validator("permissions")
    @classmethod
    def _perms(cls, v: list[str]) -> list[str]:
        allowed = {p.value for p in GRANTABLE}
        bad = [p for p in v if p not in allowed]
        if bad:
            raise ValueError(f"cannot be given to a custom role: {', '.join(bad)}")
        return sorted({"read", *v})


class AssignIn(BaseModel):
    custom_role_id: uuid.UUID | None


def _out(db: SessionDep, r: CustomRole) -> dict[str, Any]:
    n = len(list(db.scalars(select(Membership.id).where(Membership.custom_role_id == r.id))))
    return {
        "id": str(r.id),
        "name": r.name,
        "description": r.description,
        "permissions": r.permissions,
        "accounts": r.accounts,
        "teams": r.teams,
        "members": n,
    }


def _role(db: SessionDep, role_id: uuid.UUID) -> CustomRole:
    r = db.get(CustomRole, role_id)
    if r is None:
        raise NotFoundError("Role not found.")
    return r


@router.get("/custom-roles")
def list_roles(auth: Reader, db: SessionDep) -> dict[str, Any]:
    return {
        "grantable": [p.value for p in GRANTABLE],
        "roles": [_out(db, r) for r in db.scalars(select(CustomRole).order_by(CustomRole.name))],
    }


@router.post("/custom-roles", status_code=201, dependencies=[plan_feature("custom_roles")])
def create_role(body: RoleIn, auth: Admin, db: SessionDep) -> dict[str, Any]:
    assert auth.org_id is not None  # noqa: S101
    if db.scalar(select(CustomRole.id).where(CustomRole.name == body.name.strip())):
        raise AppError("A role with this name exists.", status=409, code="duplicate")
    r = CustomRole(org_id=auth.org_id, **{**body.model_dump(), "name": body.name.strip()})
    db.add(r)
    db.flush()
    return _out(db, r)


@router.put("/custom-roles/{role_id}", dependencies=[plan_feature("custom_roles")])
def update_role(role_id: uuid.UUID, body: RoleIn, auth: Admin, db: SessionDep) -> dict[str, Any]:
    r = _role(db, role_id)
    for k, v in body.model_dump().items():
        setattr(r, k, v.strip() if k == "name" else v)
    db.flush()
    return _out(db, r)


@router.delete("/custom-roles/{role_id}", status_code=204)
def delete_role(role_id: uuid.UUID, auth: Admin, db: SessionDep) -> Response:
    db.delete(_role(db, role_id))  # members fall back to their built-in role
    return Response(status_code=204)


@router.put("/members/{user_id}/custom-role", dependencies=[plan_feature("custom_roles")])
def assign(user_id: uuid.UUID, body: AssignIn, auth: Admin, db: SessionDep) -> dict[str, Any]:
    m = db.scalar(
        select(Membership).where(Membership.org_id == auth.org_id, Membership.user_id == user_id)
    )
    if m is None:
        raise NotFoundError("Member not found.")
    if m.role is Role.OWNER:
        raise AppError("Owners keep full access; a custom role cannot limit them.", code="owner")
    if user_id == auth.user.id:
        raise AppError("Ask another admin to change your own role.", code="self")
    if body.custom_role_id is not None:
        _role(db, body.custom_role_id)
    m.custom_role_id = body.custom_role_id
    db.flush()
    return {
        "user_id": str(user_id),
        "custom_role_id": str(body.custom_role_id) if body.custom_role_id else None,
    }
