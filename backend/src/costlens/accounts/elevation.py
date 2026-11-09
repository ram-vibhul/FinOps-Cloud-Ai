"""Temporary elevated access.

Someone asks for a higher role for up to 72 hours with a reason; a different admin
approves (an owner, when the request is for owner). Admins can also grant directly.
While active, the person's effective role is the higher of their membership and the
grant; it lapses on its own at ``expires_at``. Requests, decisions, revocations and
expiry are all in the audit log (the model is audited).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from costlens.accounts.roles import Role
from costlens.core.errors import AppError, ConflictError, PermissionDeniedError
from costlens.db.base import utcnow
from costlens.models import ElevatedAccess, Membership

MAX_MINUTES = 72 * 60
_RANK = {Role.VIEWER: 0, Role.MEMBER: 1, Role.ADMIN: 2, Role.OWNER: 3}


def _aware(d: datetime | None) -> datetime | None:
    return d if d is None or d.tzinfo else d.replace(tzinfo=utcnow().tzinfo)


def active_role(
    db: Session, org_id: uuid.UUID, user_id: uuid.UUID, now: datetime | None = None
) -> Role | None:
    """The highest active elevated role for this person here, if any."""
    now = now or utcnow()
    best: Role | None = None
    for g in db.scalars(
        select(ElevatedAccess).where(
            ElevatedAccess.org_id == org_id,
            ElevatedAccess.user_id == user_id,
            ElevatedAccess.status == "active",
        )
    ):
        exp = _aware(g.expires_at)
        if exp is not None and exp > now:
            r = Role(g.role)
            if best is None or _RANK[r] > _RANK[best]:
                best = r
    return best


def effective(role: Role, elevated: Role | None) -> Role:
    return elevated if elevated is not None and _RANK[elevated] > _RANK[role] else role


def _membership(db: Session, org_id: uuid.UUID, user_id: uuid.UUID) -> Membership:
    m = db.scalar(
        select(Membership).where(Membership.org_id == org_id, Membership.user_id == user_id)
    )
    if m is None:
        raise AppError(
            "Only members of this organisation can be given access.", code="not_a_member"
        )
    return m


def request(
    db: Session, org_id: uuid.UUID, user_id: uuid.UUID, role: Role, minutes: int, reason: str
) -> ElevatedAccess:
    m = _membership(db, org_id, user_id)
    if _RANK[role] <= _RANK[m.role]:
        raise AppError("You already have this role or a higher one.", code="not_higher")
    if not 15 <= minutes <= MAX_MINUTES:
        raise AppError("Choose between 15 minutes and 72 hours.", code="bad_duration")
    g = ElevatedAccess(
        org_id=org_id,
        user_id=user_id,
        role=role.value,
        minutes=minutes,
        reason=reason,
        requested_by_id=user_id,
    )
    db.add(g)
    db.flush()
    return g


def _can_decide(decider: Role, role: Role) -> bool:
    return decider is Role.OWNER if role is Role.OWNER else _RANK[decider] >= _RANK[Role.ADMIN]


def approve(
    db: Session, g: ElevatedAccess, decider_id: uuid.UUID, decider_role: Role
) -> ElevatedAccess:
    if g.status != "requested":
        raise ConflictError("This request was already decided.", code="already_decided")
    if decider_id == g.user_id:
        raise PermissionDeniedError("Someone else must approve your request.")
    if not _can_decide(decider_role, Role(g.role)):
        raise PermissionDeniedError("Only owners can approve owner access.")
    now = utcnow()
    g.status, g.decided_by_id, g.starts_at, g.expires_at = (
        "active",
        decider_id,
        now,
        now + timedelta(minutes=g.minutes),
    )
    db.flush()
    return g


def grant(
    db: Session,
    org_id: uuid.UUID,
    granter_id: uuid.UUID,
    granter_role: Role,
    user_id: uuid.UUID,
    role: Role,
    minutes: int,
    reason: str,
) -> ElevatedAccess:
    if user_id == granter_id:
        raise PermissionDeniedError("Ask someone else to grant you access.")
    if not _can_decide(granter_role, role):
        raise PermissionDeniedError("Only owners can grant owner access.")
    g = request(db, org_id, user_id, role, minutes, reason)
    g.requested_by_id = granter_id
    return approve(db, g, granter_id, granter_role)


def deny(
    db: Session, g: ElevatedAccess, decider_id: uuid.UUID, decider_role: Role
) -> ElevatedAccess:
    if g.status != "requested":
        raise ConflictError("This request was already decided.", code="already_decided")
    if not _can_decide(decider_role, Role(g.role)) and decider_id != g.user_id:
        raise PermissionDeniedError("You cannot decide this request.")
    g.status, g.decided_by_id, g.ended_at = "denied", decider_id, utcnow()
    db.flush()
    return g


def revoke(db: Session, g: ElevatedAccess, actor_id: uuid.UUID, actor_role: Role) -> ElevatedAccess:
    if g.status not in ("requested", "active"):
        raise ConflictError("This access has already ended.", code="ended")
    if actor_id != g.user_id and not _can_decide(actor_role, Role(g.role)):
        raise PermissionDeniedError("You cannot end this access.")
    g.status, g.ended_at = "revoked", utcnow()
    db.flush()
    return g


def expire(db: Session, now: datetime | None = None) -> int:
    """Mark lapsed grants as expired (so the audit log shows when they ended)."""
    now = now or utcnow()
    n = 0
    for g in db.scalars(select(ElevatedAccess).where(ElevatedAccess.status == "active")):
        exp = _aware(g.expires_at)
        if exp is not None and exp <= now:
            g.status, g.ended_at = "expired", exp
            n += 1
    db.flush()
    return n
