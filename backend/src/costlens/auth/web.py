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
from costlens.models import Organization, User, UserSession

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


def authenticate(request: Request, db: Session) -> AuthContext | None:
    """Resolve the session cookie and scope the DB session to the user and active org."""
    settings: Settings = request.app.state.settings
    token = request.cookies.get(session_cookie_name(settings))
    if not token:
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
    if record.active_org_id is not None:
        membership = acc.get_membership(db, record.active_org_id, record.user_id)
        if membership is None:  # removed from the org since; drop the stale choice
            record.active_org_id = None
            set_tenant(db, user_id=record.user_id)
        else:
            org = db.get(Organization, record.active_org_id)
            if org is None or org.deletion_requested_at is not None:
                record.active_org_id = None  # organisation is being deleted
                set_tenant(db, user_id=record.user_id)
            else:
                role = membership.role
                org_is_demo = org.is_demo
    return AuthContext(
        user=record.user,
        session=record,
        org_id=record.active_org_id,
        role=role,
        csrf_token=csrf_token(request.app.state.signing_key, token),
        org_is_demo=org_is_demo,
    )
