"""Verified-domain policies (8.9).

- **Enforce SSO**: people whose email is in one of the organisation's verified domains
  must sign in through the organisation's own sign-in provider(s). Other ways in (the
  shared Google/Microsoft buttons, local login) are refused for them. The organisation's
  owners are exempt, as a break-glass in case the identity provider is down.
- **Auto-join**: people from a verified domain who sign in become members automatically,
  with the chosen default role (never owner).

Only one organisation can verify a domain (8.2), so a person maps to at most one policy.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ccradar.accounts.roles import Role
from ccradar.auth.providers import ORG_PREFIX
from ccradar.db.tenant import set_tenant
from ccradar.models import Membership, Organization, OrgDomain, OrgIdentityProvider, User

if TYPE_CHECKING:
    from ccradar.config import Settings

log = logging.getLogger(__name__)


def policy_of(org: Organization) -> dict[str, Any]:
    p = (org.settings or {}).get("domain_policy") or {}
    return {
        "enforce_sso": bool(p.get("enforce_sso")),
        "auto_join": bool(p.get("auto_join")),
        "auto_join_role": p.get("auto_join_role", "viewer"),
    }


def org_for_email(db: Session, email: str) -> Organization | None:
    domain = email.rsplit("@", 1)[-1].lower()
    set_tenant(db, bypass=True)
    row = db.scalar(
        select(OrgDomain).where(OrgDomain.domain == domain, OrgDomain.verified_at.is_not(None))
    )
    return db.get(Organization, row.org_id) if row else None


def sso_providers(db: Session, org: Organization) -> list[str]:
    set_tenant(db, bypass=True)
    rows = db.scalars(
        select(OrgIdentityProvider.id).where(
            OrgIdentityProvider.org_id == org.id, OrgIdentityProvider.enabled.is_(True)
        )
    )
    return [f"{ORG_PREFIX}{i}" for i in rows]


def sign_in_allowed(db: Session, email: str, provider_id: str) -> tuple[bool, Organization | None]:
    """(allowed, organisation that requires its SSO when not allowed)."""
    org = org_for_email(db, email)
    if org is None or not policy_of(org)["enforce_sso"]:
        return True, None
    if provider_id in sso_providers(db, org):
        return True, None
    user = db.scalar(select(User).where(User.email == email.lower()))
    if user is not None:
        m = db.scalar(
            select(Membership).where(Membership.org_id == org.id, Membership.user_id == user.id)
        )
        if m is not None and m.role is Role.OWNER:
            return True, None  # break-glass
    return False, org


def auto_join(db: Session, user: User, settings: Settings | None = None) -> Organization | None:
    org = org_for_email(db, user.email)
    if org is None or org.deletion_requested_at is not None:
        return None
    p = policy_of(org)
    if not p["auto_join"]:
        return None
    if db.scalar(
        select(Membership.id).where(Membership.org_id == org.id, Membership.user_id == user.id)
    ):
        return None
    role = Role(p["auto_join_role"])
    set_tenant(db, org_id=org.id)
    if settings is not None:
        from ccradar.billing.usage import enforce
        from ccradar.core.errors import AppError

        try:
            enforce(db, org, settings, "members")
        except AppError:
            log.info("auto-join to %s skipped: the plan has no free seat", org.id)
            return None
    db.add(
        Membership(org_id=org.id, user_id=user.id, role=Role.VIEWER if role is Role.OWNER else role)
    )
    db.flush()
    return org
