"""Account operations: users, organisations, memberships, sessions.

Functions take a SQLAlchemy session and never commit; the caller's unit of work
(one request or job) commits. Rule violations raise :mod:`costlens.core.errors`
exceptions, which the API turns into problem+json responses.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from costlens.accounts.roles import Permission, Role, can_assign, has_permission
from costlens.core.errors import AppError, ConflictError, NotFoundError, PermissionDeniedError
from costlens.db.base import utcnow
from costlens.db.tenant import set_tenant, set_user
from costlens.models import Identity, Membership, Organization, User, UserSession

SESSION_TTL = timedelta(days=14)
SESSION_TOUCH_INTERVAL = timedelta(minutes=5)
_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def normalize_email(email: str) -> str:
    email = email.strip().lower()
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        raise AppError("Not a valid email address.", code="invalid_email")
    return email


# --- users ------------------------------------------------------------------


def sign_in_with_identity(
    db: Session,
    *,
    provider: str,
    subject: str,
    email: str,
    email_verified: bool,
    name: str = "",
    avatar_url: str | None = None,
) -> User:
    """Find or create the user behind an identity-provider login.

    An existing account is linked by email only when the provider says the
    email is verified; otherwise someone could claim another person's account.
    """
    email = normalize_email(email)
    identity = db.scalar(
        select(Identity).where(Identity.provider == provider, Identity.subject == subject)
    )
    if identity is not None:
        user = identity.user
        identity.email = email
    else:
        user = db.scalar(select(User).where(User.email == email))
        if user is not None and not email_verified:
            raise PermissionDeniedError(
                "This email belongs to an existing account; the sign-in provider did not "
                "confirm you own it.",
                code="email_not_verified",
            )
        if user is None:
            user = User(
                email=email,
                name=name or email.split("@")[0],
                avatar_url=avatar_url,
                is_active=True,
                is_platform_admin=False,
            )
            db.add(user)
        user.identities.append(Identity(provider=provider, subject=subject, email=email))
    if not user.is_active:
        raise PermissionDeniedError("This account is disabled.", code="account_disabled")
    if name and not user.name:
        user.name = name
    if avatar_url:
        user.avatar_url = avatar_url
    user.last_login_at = utcnow()
    db.flush()
    return user


# --- organisations ----------------------------------------------------------


def slugify(name: str) -> str:
    slug = _SLUG_STRIP.sub("-", name.lower()).strip("-")[:50].strip("-")
    return slug or "org"


def _unique_slug(db: Session, name: str) -> str:
    base = slugify(name)
    slug = base
    while db.scalar(
        select(func.count()).select_from(Organization).where(Organization.slug == slug)
    ):
        slug = f"{base}-{secrets.token_hex(3)}"
    return slug


def create_organization(
    db: Session, *, name: str, owner: User, is_demo: bool = False
) -> Organization:
    """Create an organisation with ``owner`` as its first owner.

    Switches the session's tenant context to the new organisation (as ``owner``).
    """
    name = name.strip()
    if not 1 <= len(name) <= 200:
        raise AppError("Organisation name must be 1 to 200 characters.", code="invalid_name")
    org = Organization(name=name, slug=_unique_slug(db, name), is_demo=is_demo)
    db.add(org)
    db.flush()
    set_tenant(db, org_id=org.id, user_id=owner.id)
    db.add(Membership(org_id=org.id, user_id=owner.id, role=Role.OWNER))
    db.flush()
    return org


def user_organizations(db: Session, user_id: uuid.UUID) -> list[tuple[Organization, Role]]:
    rows = db.execute(
        select(Organization, Membership.role)
        .join(Membership, Membership.org_id == Organization.id)
        .where(Membership.user_id == user_id, Organization.deletion_requested_at.is_(None))
        .order_by(Organization.name)
    ).all()
    return [(org, role) for org, role in rows]


def get_membership(db: Session, org_id: uuid.UUID, user_id: uuid.UUID) -> Membership | None:
    return db.scalar(
        select(Membership).where(Membership.org_id == org_id, Membership.user_id == user_id)
    )


def require_permission(
    db: Session, org_id: uuid.UUID, user_id: uuid.UUID, permission: Permission
) -> Membership:
    membership = get_membership(db, org_id, user_id)
    if membership is None:
        # Same answer as a missing organisation: do not reveal that it exists.
        raise NotFoundError("Organisation not found.")
    if not has_permission(membership.role, permission):
        raise PermissionDeniedError("Your role does not allow this.")
    return membership


def _owner_count(db: Session, org_id: uuid.UUID) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(Membership)
            .where(Membership.org_id == org_id, Membership.role == Role.OWNER)
        )
        or 0
    )


def add_member(
    db: Session, *, org_id: uuid.UUID, actor_id: uuid.UUID, user: User, role: Role
) -> Membership:
    actor = require_permission(db, org_id, actor_id, Permission.MANAGE_MEMBERS)
    if not can_assign(actor.role, None, role):
        raise PermissionDeniedError("Only owners can add owners.")
    if get_membership(db, org_id, user.id) is not None:
        raise ConflictError("Already a member.", code="already_member")
    membership = Membership(org_id=org_id, user_id=user.id, role=role)
    db.add(membership)
    db.flush()
    return membership


def change_role(
    db: Session, *, org_id: uuid.UUID, actor_id: uuid.UUID, user_id: uuid.UUID, role: Role
) -> Membership:
    actor = require_permission(db, org_id, actor_id, Permission.MANAGE_MEMBERS)
    target = get_membership(db, org_id, user_id)
    if target is None:
        raise NotFoundError("Member not found.")
    if not can_assign(actor.role, target.role, role):
        raise PermissionDeniedError("Only owners can change an owner or make someone owner.")
    if target.role is Role.OWNER and role is not Role.OWNER and _owner_count(db, org_id) == 1:
        raise ConflictError("An organisation needs at least one owner.", code="last_owner")
    target.role = role
    db.flush()
    return target


def remove_member(
    db: Session, *, org_id: uuid.UUID, actor_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    """Remove a member, or leave the organisation when ``actor_id == user_id``."""
    target = get_membership(db, org_id, user_id)
    if actor_id != user_id:
        actor = require_permission(db, org_id, actor_id, Permission.MANAGE_MEMBERS)
        if target is None:
            raise NotFoundError("Member not found.")
        if not can_assign(actor.role, target.role, target.role):
            raise PermissionDeniedError("Only owners can remove an owner.")
    elif target is None:
        raise NotFoundError("Organisation not found.")
    if target.role is Role.OWNER and _owner_count(db, org_id) == 1:
        raise ConflictError(
            "The last owner cannot leave; transfer ownership first.", code="last_owner"
        )
    db.delete(target)
    db.flush()


# --- sessions -----------------------------------------------------------------


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(
    db: Session,
    user: User,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
    ttl: timedelta = SESSION_TTL,
    now: datetime | None = None,
) -> tuple[str, UserSession]:
    """Create a session; returns the raw token (shown once, sent as a cookie).

    Sets the session's acting user so the user's organisations are readable.
    """
    now = now or utcnow()
    set_user(db, user.id)
    token = secrets.token_urlsafe(32)
    orgs = user_organizations(db, user.id)
    record = UserSession(
        user_id=user.id,
        token_hash=hash_token(token),
        expires_at=now + ttl,
        last_seen_at=now,
        ip=ip,
        user_agent=(user_agent or "")[:500] or None,
        active_org_id=orgs[0][0].id if orgs else None,
    )
    db.add(record)
    db.flush()
    return token, record


def _aware(value: datetime) -> datetime:
    # SQLite returns naive datetimes; everything we store is UTC.
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def resolve_session(db: Session, token: str, *, now: datetime | None = None) -> UserSession | None:
    """Return the live session for a raw token, or None."""
    if not token or len(token) > 256:
        return None
    now = now or utcnow()
    record = db.scalar(select(UserSession).where(UserSession.token_hash == hash_token(token)))
    if record is None or record.revoked_at is not None or _aware(record.expires_at) <= now:
        return None
    if not record.user.is_active:
        return None
    last = record.last_seen_at
    if last is None or now - _aware(last) >= SESSION_TOUCH_INTERVAL:
        record.last_seen_at = now
        db.flush()
    return record


def switch_organization(db: Session, record: UserSession, org_id: uuid.UUID) -> None:
    if get_membership(db, org_id, record.user_id) is None:
        raise NotFoundError("Organisation not found.")
    record.active_org_id = org_id
    db.flush()


def revoke_session(db: Session, record: UserSession) -> None:
    record.revoked_at = utcnow()
    db.flush()


def revoke_all_sessions(db: Session, user_id: uuid.UUID) -> int:
    records = db.scalars(
        select(UserSession).where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
    ).all()
    for record in records:
        record.revoked_at = utcnow()
    db.flush()
    return len(records)
