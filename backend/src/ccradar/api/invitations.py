"""Invite people to an organisation; preview and accept invitations."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ccradar.accounts import invitations as inv_svc
from ccradar.accounts import service as acc
from ccradar.accounts.roles import Permission, Role
from ccradar.api.deps import CurrentAuth, SessionDep, SettingsDep, require
from ccradar.auth.web import AuthContext
from ccradar.core.errors import NotFoundError
from ccradar.models import Invitation, Organization, User

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


def _tell_existing_user(db: Session, org: Organization, email: str, inviter: User) -> None:
    """Someone who already uses CloudCostRadar also sees the invitation in their inbox (9.4).
    The link itself is only in the email: the inbox never holds the secret token."""
    from ccradar.core.inbox import deliver
    from ccradar.core.notify import Message

    user = db.scalar(select(User).where(func.lower(User.email) == email.strip().lower()))
    if user is None:
        return
    who = inviter.name or inviter.email
    message = Message(
        title=f"{who} invited you to {org.name}",
        text="Open the link in the invitation email we sent you to join.",
        severity="info",
    )
    deliver(db, "invitation", [message], user_ids=[user.id])


@router.post("/api/v1/org/invitations", response_model=CreatedInvitation, status_code=201)
def invite(
    body: InviteIn, auth: Manager, db: SessionDep, settings: SettingsDep, request: Request
) -> CreatedInvitation:
    assert auth.org_id is not None  # noqa: S101 - guaranteed by require()
    from ccradar.billing.usage import enforce

    current = db.get(Organization, auth.org_id)
    assert current is not None  # noqa: S101
    enforce(db, current, settings, "members")
    token, inv = inv_svc.create_invitation(
        db, org_id=auth.org_id, actor_id=auth.user.id, email=body.email, role=body.role
    )
    url = f"{settings.base_url.rstrip('/')}/invite/{token}"
    org = db.get(Organization, auth.org_id)
    assert org is not None  # noqa: S101
    message = inv_svc.invitation_email(inv=inv, org=org, inviter=auth.user, url=url)
    request.app.state.jobs.enqueue("email.send", message=message.to_dict())
    _tell_existing_user(db, org, inv.email, auth.user)
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
