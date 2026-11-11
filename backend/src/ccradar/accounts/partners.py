"""Partner mode (8.5): partners manage client organisations.

- A client organisation points at its partner (``partner_id``). Admins and owners of the
  partner get access to every client, with the role stored on the link:
  ``owner`` for clients the partner created, ``admin`` for organisations that linked
  themselves with a partner code. Client staff with their own membership keep it.
- Organisations become partners through Yugiverse (platform admins) here, or the
  sign-up flow in 8.13.
- Clients can leave their partner at any time (owners).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ccradar.accounts.roles import Role
from ccradar.db.base import utcnow
from ccradar.models import Membership, Organization

STAFF = (Role.ADMIN, Role.OWNER)
CODE_DAYS = 7


def access_role(db: Session, org: Organization, user_id: uuid.UUID) -> Role | None:
    """The role a partner's staff member has in a client organisation, if any."""
    if org.partner_id is None:
        return None
    m = db.scalar(
        select(Membership).where(Membership.org_id == org.partner_id, Membership.user_id == user_id)
    )
    if m is None or m.role not in STAFF:
        return None
    return Role(((org.settings or {}).get("partner") or {}).get("role", "admin"))


def clients(db: Session, partner_id: uuid.UUID) -> list[Organization]:
    return list(
        db.scalars(
            select(Organization)
            .where(
                Organization.partner_id == partner_id, Organization.deletion_requested_at.is_(None)
            )
            .order_by(Organization.name)
        )
    )


def client_orgs_for(db: Session, user_id: uuid.UUID) -> list[tuple[Organization, Role]]:
    """Client organisations a person reaches through partner staff roles."""
    out = []
    for m in db.scalars(
        select(Membership).where(Membership.user_id == user_id, Membership.role.in_(STAFF))
    ):
        partner = db.get(Organization, m.org_id)
        if partner is None or not partner.is_partner:
            continue
        for c in clients(db, partner.id):
            role = access_role(db, c, user_id)
            if role is not None:
                out.append((c, role))
    return out


def _digest(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def new_link_code(partner: Organization) -> str:
    code = "clp_" + secrets.token_urlsafe(18)
    codes = [
        c
        for c in ((partner.settings or {}).get("link_codes") or [])
        if c["expires"] > utcnow().isoformat()
    ]
    codes.append(
        {"hash": _digest(code), "expires": (utcnow() + timedelta(days=CODE_DAYS)).isoformat()}
    )
    partner.settings = {**(partner.settings or {}), "link_codes": codes[-20:]}
    return code


def partner_for_code(db: Session, code: str, *, use: bool = False) -> Organization | None:
    """The partner a valid code belongs to. ``use=True`` spends it (codes work once)."""
    digest, now = _digest(code.strip()), utcnow().isoformat()
    for p in db.scalars(select(Organization).where(Organization.is_partner.is_(True))):
        codes = (p.settings or {}).get("link_codes") or []
        for c in codes:
            if hmac.compare_digest(c["hash"], digest) and c["expires"] > now:
                if use:
                    p.settings = {
                        **(p.settings or {}),
                        "link_codes": [x for x in codes if x is not c],
                    }
                return p
    return None
