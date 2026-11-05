"""Invitations: an admin invites an email address; that person signs in and accepts.

The link only works for a signed-in user whose (verified) email matches the
invitation, so a forwarded or leaked link cannot be used by someone else.
"""

from __future__ import annotations

import html
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from costlens.accounts import service as acc
from costlens.accounts.roles import Permission, Role, can_assign
from costlens.core.email import Email
from costlens.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from costlens.db.base import utcnow
from costlens.db.tenant import current_tenant, set_tenant
from costlens.models import Invitation, Membership, Organization, User

INVITATION_TTL = timedelta(days=7)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def status(inv: Invitation, now: datetime | None = None) -> str:
    now = now or utcnow()
    if inv.accepted_at:
        return "accepted"
    if inv.revoked_at:
        return "revoked"
    if _aware(inv.expires_at) <= now:
        return "expired"
    return "pending"


def create_invitation(
    db: Session, *, org_id: uuid.UUID, actor_id: uuid.UUID, email: str, role: Role
) -> tuple[str, Invitation]:
    actor = acc.require_permission(db, org_id, actor_id, Permission.MANAGE_MEMBERS)
    if not can_assign(actor.role, None, role):
        raise PermissionDeniedError("Only owners can invite owners.")
    email = acc.normalize_email(email)
    existing_user = db.scalar(select(User).where(User.email == email))
    if existing_user and acc.get_membership(db, org_id, existing_user.id):
        raise ConflictError("This person is already a member.", code="already_member")
    for old in db.scalars(
        select(Invitation).where(
            Invitation.email == email,
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
        )
    ):
        old.revoked_at = utcnow()  # a new invitation replaces any pending one
    token = secrets.token_urlsafe(32)
    inv = Invitation(
        org_id=org_id,
        email=email,
        role=role,
        token_hash=acc.hash_token(token),
        invited_by_id=actor_id,
        expires_at=utcnow() + INVITATION_TTL,
    )
    db.add(inv)
    db.flush()
    return token, inv


def revoke_invitation(db: Session, *, actor_id: uuid.UUID, invitation_id: uuid.UUID) -> None:
    inv = db.get(Invitation, invitation_id)
    if inv is None:
        raise NotFoundError("Invitation not found.")
    acc.require_permission(db, inv.org_id, actor_id, Permission.MANAGE_MEMBERS)
    if status(inv) != "pending":
        raise ConflictError("This invitation is no longer pending.", code="not_pending")
    inv.revoked_at = utcnow()
    db.flush()


def find_by_token(db: Session, token: str) -> Invitation | None:
    """Looks across organisations (the invitee is not a member yet)."""
    if not token or len(token) > 256:
        return None
    previous = current_tenant(db)
    set_tenant(db, bypass=True)
    try:
        return db.scalar(select(Invitation).where(Invitation.token_hash == acc.hash_token(token)))
    finally:
        set_tenant(db, org_id=previous.org_id, user_id=previous.user_id, bypass=previous.bypass)


def accept_invitation(db: Session, *, token: str, user: User) -> Membership:
    inv = find_by_token(db, token)
    if inv is None:
        raise NotFoundError("This invitation link is not valid.")
    state = status(inv)
    if state != "pending":
        raise ConflictError(f"This invitation is {state}.", code=f"invitation_{state}")
    if inv.email != user.email:
        hint = mask_email(inv.email)
        raise PermissionDeniedError(
            f"This invitation is for a different email address. Sign in as {hint}.",
            code="wrong_account",
        )
    set_tenant(db, org_id=inv.org_id, user_id=user.id)
    membership = acc.get_membership(db, inv.org_id, user.id)
    if membership is None:
        membership = Membership(org_id=inv.org_id, user_id=user.id, role=inv.role)
        db.add(membership)
    inv.accepted_at = utcnow()
    inv.accepted_by_id = user.id
    db.flush()
    return membership


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:2]}{'*' * max(1, len(local) - 2)}@{domain}"


def invitation_email(
    *, inv: Invitation, org: Organization, inviter: User | None, url: str
) -> Email:
    who = inviter.name or inviter.email if inviter else "A colleague"
    text = (
        f"{who} invited you to join {org.name} on CostLens as {inv.role.value}.\n\n"
        f"Accept the invitation: {url}\n\n"
        f"The link works for {inv.email} and expires in 7 days. "
        "If you did not expect this, you can ignore this email."
    )
    e = html.escape
    body = (
        f"<p>{e(who)} invited you to join <strong>{e(org.name)}</strong> on CostLens "
        f"as <strong>{e(inv.role.value)}</strong>.</p>"
        f'<p><a href="{e(url)}">Accept the invitation</a></p>'
        f"<p>The link works for {e(inv.email)} and expires in 7 days. If you did not "
        "expect this, you can ignore this email.</p>"
    )
    return Email(
        to=inv.email,
        subject=f"Join {org.name} on CostLens",
        text=text,
        html=body,
        tags={"kind": "invitation"},
    )
