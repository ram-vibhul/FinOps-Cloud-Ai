"""Organisation API keys: issue, resolve and the rules for what a key may do."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from costlens.accounts.roles import Role
from costlens.db.base import utcnow
from costlens.db.tenant import set_tenant
from costlens.models import Membership, OrgApiKey, User

PREFIX = "clk"
SCOPES = {"read": Role.VIEWER, "write": Role.MEMBER, "admin": Role.ADMIN}
_RANK = {Role.VIEWER: 0, Role.MEMBER: 1, Role.ADMIN: 2, Role.OWNER: 3}
# Things only a person in a browser may do: sign-in and sessions, their own account,
# switching or creating organisations, invitations, members, SSO, exports, deleting the
# organisation, and managing API keys themselves.
BLOCKED = re.compile(
    r"^/api/v1/(auth/|me(/|$)|organizations|invitations/|demo|platform/|org/partner$|"
    r"partner-invites/|org/partner-application$|"
    r"org/(api-keys|sso|members|invitations|exports)|org$)"
)


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def issue(
    db: Session, org_id: uuid.UUID, user_id: uuid.UUID, name: str, scope: str
) -> tuple[OrgApiKey, str]:
    secret = secrets.token_urlsafe(32)
    key = OrgApiKey(
        org_id=org_id,
        name=name,
        scope=scope,
        key_hash=_digest(secret),
        key_hint=secret[-6:],
        created_by_id=user_id,
    )
    db.add(key)
    db.flush()
    return key, f"{PREFIX}_{secret}"


def lower(a: Role, b: Role) -> Role:
    return a if _RANK[a] <= _RANK[b] else b


def resolve(db: Session, token: str) -> tuple[OrgApiKey, User, Role] | None:
    """The key, the person it acts for, and the role it acts with; None if not valid."""
    if not token.startswith(f"{PREFIX}_"):
        return None
    digest = _digest(token.removeprefix(f"{PREFIX}_"))
    set_tenant(db, bypass=True)
    key = db.scalar(select(OrgApiKey).where(OrgApiKey.key_hash == digest))
    now = utcnow()
    if key is None or not hmac.compare_digest(key.key_hash, digest) or key.revoked_at is not None:
        return None
    if (
        key.expires_at is not None
        and key.expires_at.replace(tzinfo=key.expires_at.tzinfo or now.tzinfo) <= now
    ):
        return None
    membership = db.scalar(
        select(Membership).where(
            Membership.org_id == key.org_id, Membership.user_id == key.created_by_id
        )
    )
    user = db.get(User, key.created_by_id)
    if membership is None or user is None or not user.is_active:
        return None
    if (
        key.last_used_at is None
        or (now - key.last_used_at.replace(tzinfo=now.tzinfo)).total_seconds() > 60
    ):
        key.last_used_at = now
    from costlens.accounts.elevation import active_role, effective

    role = effective(membership.role, active_role(db, key.org_id, user.id))
    return key, user, lower(SCOPES[key.scope], role)
