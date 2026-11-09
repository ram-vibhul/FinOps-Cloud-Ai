"""Cookies and request authentication shared by the auth routes and dependencies."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import Request, Response
from sqlalchemy.orm import Session

from costlens.accounts import service as acc
from costlens.accounts.roles import Role
from costlens.audit.recorder import Actor, set_actor
from costlens.config import Settings
from costlens.core.csrf import CSRF_COOKIE, csrf_token
from costlens.db.tenant import set_tenant
from costlens.models import Membership, Organization, User, UserSession

STATE_COOKIE = "costlens_oidc"
STATE_COOKIE_PATH = "/api/v1/auth"
STATE_MAX_AGE = 600


def session_cookie_name(settings: Settings) -> str:
    # __Host- prefix: browser enforces Secure, path=/ and no Domain (no subdomain games).
    return "__Host-costlens_session" if settings.secure_cookies else "costlens_session"


def set_session_cookie(
    response: Response, settings: Settings, token: str, signing_key: str
) -> None:
    # Readable by the web UI (not HttpOnly): it sends it back in X-CSRF-Token.
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token(signing_key, token),
        max_age=int(acc.SESSION_TTL.total_seconds()),
        path="/",
        secure=settings.secure_cookies,
        httponly=False,
        samesite="lax",
    )
    response.set_cookie(
        session_cookie_name(settings),
        token,
        max_age=int(acc.SESSION_TTL.total_seconds()),
        path="/",
        secure=settings.secure_cookies,
        httponly=True,
        samesite="lax",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(CSRF_COOKIE, path="/", secure=settings.secure_cookies)
    response.delete_cookie(
        session_cookie_name(settings),
        path="/",
        secure=settings.secure_cookies,
        httponly=True,
        samesite="lax",
    )


def safe_return_to(value: str | None) -> str:
    """Only same-site absolute paths; blocks open redirects like //evil.com or /\\evil."""
    if not value or not value.startswith("/") or value.startswith(("//", "/\\")):
        return "/"
    if any(c in value for c in "\r\n") or len(value) > 500:
        return "/"
    return value


@dataclass
class AuthContext:
    user: User
    session: UserSession
    org_id: uuid.UUID | None
    role: Role | None
    csrf_token: str = ""
    org_is_demo: bool = False
    api_key_id: uuid.UUID | None = None  # set when an organisation API key made the request
    api_key_can_investigate: bool = False  # 8.14: MCP may start investigations with this key
    # Custom role (8.8): its permissions replace the role's; scope limits accounts/teams.
    permissions: frozenset[str] | None = None
    scope: dict[str, list[str]] | None = None


def authenticate(request: Request, db: Session) -> AuthContext | None:
    """Resolve the session cookie and scope the DB session to the user and active org."""
    settings: Settings = request.app.state.settings
    token = request.cookies.get(session_cookie_name(settings))
    if not token:
        bearer = request.headers.get("authorization", "")
        if bearer.startswith("Bearer clk_"):
            return _api_key_auth(request, db, bearer.removeprefix("Bearer ").strip())
        return None
    record = acc.resolve_session(db, token)
    if record is None:
        return None
    set_tenant(db, org_id=record.active_org_id, user_id=record.user_id)
    set_actor(
        db,
        Actor(
            type="user",
            user_id=record.user_id,
            label=record.user.email,
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        ),
    )
    role = None
    org_is_demo = False
    permissions: frozenset[str] | None = None
    scope: dict[str, list[str]] | None = None
    if record.active_org_id is not None:
        membership = acc.get_membership(db, record.active_org_id, record.user_id)
        if membership is None:  # partner staff reach client organisations without one
            from costlens.accounts.partners import access_role

            client = db.get(Organization, record.active_org_id)
            via_partner = access_role(db, client, record.user_id) if client else None
            if via_partner is not None and client is not None:
                membership = Membership(org_id=client.id, user_id=record.user_id, role=via_partner)
        if membership is None:  # removed from the org since; drop the stale choice
            record.active_org_id = None
            set_tenant(db, user_id=record.user_id)
        else:
            org = db.get(Organization, record.active_org_id)
            if org is None or org.deletion_requested_at is not None:
                record.active_org_id = None  # organisation is being deleted
                set_tenant(db, user_id=record.user_id)
            else:
                from costlens.accounts.custom_roles import context
                from costlens.accounts.elevation import active_role, effective

                elevated = active_role(db, record.active_org_id, record.user_id)
                role = effective(membership.role, elevated)
                if elevated is None:  # temporary access replaces a custom role while it lasts
                    permissions, scope = context(db, membership)
                org_is_demo = org.is_demo
    return AuthContext(
        user=record.user,
        session=record,
        org_id=record.active_org_id,
        role=role,
        csrf_token=csrf_token(request.app.state.signing_key, token),
        org_is_demo=org_is_demo,
        permissions=permissions,
        scope=scope,
    )


def _api_key_auth(request: Request, db: Session, token: str) -> AuthContext | None:
    from costlens.accounts.api_keys import resolve

    found = resolve(db, token)
    if found is None:
        return None
    key, user, role = found
    set_tenant(db, org_id=key.org_id, user_id=user.id)
    set_actor(
        db,
        Actor(
            type="api_key",
            user_id=user.id,
            label=f"API key {key.name} (…{key.key_hint}) for {user.email}",
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        ),
    )
    org = db.get(Organization, key.org_id)
    if org is None or org.deletion_requested_at is not None:
        return None
    from costlens.billing.metering import record_api_call

    record_api_call(db, request.app.state.settings, key.org_id)  # B.12: counted and limited
    # A stand-in session that is never stored: API keys have no browser session.
    session = UserSession(user_id=user.id, active_org_id=key.org_id)
    # The creator's custom role (8.8) limits the key too, within the key's own scope.
    from costlens.accounts.custom_roles import context
    from costlens.accounts.roles import Permission, has_permission

    membership = acc.get_membership(db, key.org_id, user.id)
    permissions, scope = context(db, membership)
    if permissions is not None:
        permissions = frozenset(p for p in permissions if has_permission(role, Permission(p)))
    return AuthContext(
        user=user,
        session=session,
        org_id=key.org_id,
        role=role,
        org_is_demo=org.is_demo,
        api_key_id=key.id,
        api_key_can_investigate=bool(key.can_investigate),
        permissions=permissions,
        scope=scope,
    )
