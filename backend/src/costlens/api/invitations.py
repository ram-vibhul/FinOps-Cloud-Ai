"""Invite people to an organisation; preview and accept invitations."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from costlens.accounts import invitations as inv_svc
from costlens.accounts import service as acc
from costlens.accounts.roles import Permission, Role
from costlens.api.deps import CurrentAuth, SessionDep, SettingsDep, require
from costlens.auth.web import AuthContext
from costlens.core.errors import NotFoundError
from costlens.models import Invitation, Organization

router = APIRouter(tags=["invitations"])
Manager = Annotated[AuthContext, require(Permission.MANAGE_MEMBERS)]


class InviteIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    role: Role = Role.MEMBER


class InvitationOut(BaseModel):
    id: uuid.UUID
    email: str
    role: Role
    status: str
    expires_at: datetime
    invited_by: str | None


class CreatedInvitation(InvitationOut):
    # Shown once to the admin so the link can be shared even without email delivery.
    invite_url: str


class InvitationPreview(BaseModel):
    organization: str
    role: Role
    email_hint: str
    invited_by: str | None
    status: str


def _out(inv: Invitation) -> InvitationOut:
    inviter = inv.invited_by
    return InvitationOut(
        id=inv.id,
        email=inv.email,
        role=inv.role,
        status=inv_svc.status(inv),
        expires_at=inv.expires_at,
        invited_by=(inviter.name or inviter.email) if inviter else None,
    )


@router.post("/api/v1/org/invitations", response_model=CreatedInvitation, status_code=201)
def invite(
    body: InviteIn, auth: Manager, db: SessionDep, settings: SettingsDep, request: Request
) -> CreatedInvitation:
    assert auth.org_id is not None  # noqa: S101 - guaranteed by require()
    token, inv = inv_svc.create_invitation(
        db, org_id=auth.org_id, actor_id=auth.user.id, email=body.email, role=body.role
    )
    url = f"{settings.base_url.rstrip('/')}/invite/{token}"
    org = db.get(Organization, auth.org_id)
    assert org is not None  # noqa: S101
    message = inv_svc.invitation_email(inv=inv, org=org, inviter=auth.user, url=url)
    request.app.state.jobs.enqueue("email.send", message=message.to_dict())
    return CreatedInvitation(**_out(inv).model_dump(), invite_url=url)


@router.get("/api/v1/org/invitations", response_model=list[InvitationOut])
def list_invitations(auth: Manager, db: SessionDep) -> list[InvitationOut]:
    rows = db.scalars(select(Invitation).order_by(Invitation.created_at.desc())).all()
    return [_out(r) for r in rows if inv_svc.status(r) == "pending"]


@router.delete("/api/v1/org/invitations/{invitation_id}", status_code=204)
def revoke(invitation_id: uuid.UUID, auth: Manager, db: SessionDep) -> Response:
    inv_svc.revoke_invitation(db, actor_id=auth.user.id, invitation_id=invitation_id)
    return Response(status_code=204)


@router.get("/api/v1/invitations/{token}", response_model=InvitationPreview)
def preview(token: str, db: SessionDep) -> InvitationPreview:
    """What the invitation page shows before (or after) signing in."""
    inv = inv_svc.find_by_token(db, token)
    if inv is None:
        raise NotFoundError("This invitation link is not valid.")
    org = db.get(Organization, inv.org_id)
    inviter = inv.invited_by
    return InvitationPreview(
        organization=org.name if org else "",
        role=inv.role,
        email_hint=inv_svc.mask_email(inv.email),
        invited_by=(inviter.name or inviter.email) if inviter else None,
        status=inv_svc.status(inv),
    )


@router.post("/api/v1/invitations/{token}/accept", status_code=204)
def accept(token: str, auth: CurrentAuth, db: SessionDep) -> Response:
    membership = inv_svc.accept_invitation(db, token=token, user=auth.user)
    acc.switch_organization(db, auth.session, membership.org_id)
    return Response(status_code=204)
