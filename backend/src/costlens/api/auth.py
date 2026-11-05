"""Sign-in (OpenID Connect), current user, sign-out."""

from __future__ import annotations

import logging
import secrets
import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from costlens.accounts import service as acc
from costlens.accounts.roles import Role
from costlens.api.deps import CurrentAuth, SessionDep, SettingsDep
from costlens.auth.oidc import OIDCClient, OIDCError, new_pkce_pair
from costlens.auth.providers import configured_providers, resolve_provider
from costlens.auth.web import (
    STATE_COOKIE,
    STATE_COOKIE_PATH,
    STATE_MAX_AGE,
    clear_session_cookie,
    safe_return_to,
    set_session_cookie,
)
from costlens.config import DeploymentMode, Settings
from costlens.core.errors import AppError
from costlens.core.signing import BadSignatureError, Signer

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
log = logging.getLogger(__name__)


class ProviderOut(BaseModel):
    id: str
    name: str


class OrganizationOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    role: Role
    is_demo: bool


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    name: str
    avatar_url: str | None


class MeOut(BaseModel):
    user: UserOut
    organizations: list[OrganizationOut]
    active_organization_id: uuid.UUID | None
    role: Role | None
    csrf_token: str


def _client(request: Request, db: Session, provider_id: str) -> OIDCClient:
    provider = resolve_provider(db, request.app.state.settings, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Unknown sign-in provider.")
    clients: dict[str, OIDCClient] = request.app.state.oidc_clients
    cached = clients.get(provider_id)
    if cached is None or cached.provider != provider:  # config changed: drop caches
        cached = OIDCClient(provider, request.app.state.http, request.app.state.resolver)
        clients[provider_id] = cached
    return cached


def _signer(request: Request) -> Signer:
    return Signer(request.app.state.signing_key, "oidc-state")


def _redirect_uri(settings: Settings, provider_id: str) -> str:
    return f"{settings.base_url.rstrip('/')}/api/v1/auth/callback/{provider_id}"


def _fail(code: str) -> RedirectResponse:
    response = RedirectResponse(f"/sign-in?error={code}", status_code=303)
    response.delete_cookie(STATE_COOKIE, path=STATE_COOKIE_PATH)
    return response


@router.get("/providers", response_model=list[ProviderOut])
def providers(settings: SettingsDep) -> list[ProviderOut]:
    """Sign-in options configured on this instance (``local`` = development sign-in)."""
    found = [
        ProviderOut(id=p.id, name=p.display_name) for p in configured_providers(settings).values()
    ]
    if local_login_allowed(settings):
        found.append(ProviderOut(id="local", name="Local account (development)"))
    return found


def local_login_allowed(settings: Settings) -> bool:
    return settings.local_login and settings.deployment_mode is DeploymentMode.LOCAL


class LocalLoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    name: str = Field(default="", max_length=200)


@router.post("/local", status_code=204, include_in_schema=False)
def local_login(
    body: LocalLoginIn, request: Request, db: SessionDep, settings: SettingsDep
) -> Response:
    """Development/trial sign-in without an identity provider (local mode + opt-in only)."""
    if not local_login_allowed(settings):
        raise HTTPException(status_code=404)
    user = acc.sign_in_with_identity(
        db,
        provider="local",
        subject=body.email.strip().lower(),
        email=body.email,
        email_verified=True,
        name=body.name,
    )
    token, _ = acc.create_session(
        db,
        user,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    response = Response(status_code=204)
    set_session_cookie(response, settings, token, request.app.state.signing_key)
    return response


@router.get("/organizations/{slug}/providers", response_model=list[ProviderOut])
def organization_providers(slug: str, db: SessionDep) -> list[ProviderOut]:
    """Single sign-on options of one organisation, for its sign-in page."""
    from sqlalchemy import select

    from costlens.db.tenant import set_tenant
    from costlens.models import Organization, OrgIdentityProvider

    set_tenant(db, bypass=True)
    org = db.scalar(select(Organization).where(Organization.slug == slug))
    if org is None:
        return []
    rows = db.scalars(
        select(OrgIdentityProvider).where(
            OrgIdentityProvider.org_id == org.id, OrgIdentityProvider.enabled.is_(True)
        )
    ).all()
    set_tenant(db)
    return [ProviderOut(id=r.provider_id, name=r.display_name) for r in rows]


@router.get("/login/{provider_id}", include_in_schema=False)
def login(
    provider_id: str,
    request: Request,
    settings: SettingsDep,
    db: SessionDep,
    return_to: Annotated[str | None, Query(max_length=500)] = None,
) -> RedirectResponse:
    client = _client(request, db, provider_id)
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    verifier, challenge = new_pkce_pair()
    try:
        url = client.authorization_url(
            redirect_uri=_redirect_uri(settings, provider_id),
            state=state,
            nonce=nonce,
            code_challenge=challenge,
        )
    except OIDCError as exc:
        log.warning("sign-in start failed: %s", exc)
        return _fail(exc.code)
    cookie = _signer(request).dumps(
        {"p": provider_id, "s": state, "n": nonce, "v": verifier, "r": safe_return_to(return_to)},
        max_age=STATE_MAX_AGE,
    )
    response = RedirectResponse(url, status_code=303)
    response.set_cookie(
        STATE_COOKIE,
        cookie,
        max_age=STATE_MAX_AGE,
        path=STATE_COOKIE_PATH,
        secure=settings.secure_cookies,
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/callback/{provider_id}", include_in_schema=False)
def callback(  # noqa: PLR0911 - each early return is a distinct sign-in failure
    provider_id: str,
    request: Request,
    db: SessionDep,
    settings: SettingsDep,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    if error:
        return _fail("access_denied" if error == "access_denied" else "provider_error")
    try:
        saved = _signer(request).loads(request.cookies.get(STATE_COOKIE, ""))
    except (BadSignatureError, ValueError, KeyError):
        return _fail("state_expired")
    if (
        not code
        or not state
        or saved.get("p") != provider_id
        or not secrets.compare_digest(str(saved.get("s")), state)
    ):
        return _fail("state_mismatch")
    client = _client(request, db, provider_id)
    try:
        tokens = client.exchange_code(
            code=code, redirect_uri=_redirect_uri(settings, provider_id), code_verifier=saved["v"]
        )
        claims = client.verify_id_token(tokens["id_token"], nonce=saved["n"])
    except OIDCError as exc:
        log.warning("sign-in with %s failed: %s (%s)", provider_id, exc.code, exc)
        return _fail(exc.code)

    domain = claims.email.rsplit("@", 1)[-1].lower()
    allowed = [d.lower() for d in settings.allowed_email_domains]
    if allowed and domain not in allowed:
        return _fail("domain_not_allowed")
    try:
        user = acc.sign_in_with_identity(
            db,
            provider=provider_id,
            subject=claims.subject,
            email=claims.email,
            email_verified=claims.email_verified,
            name=claims.name,
            avatar_url=claims.picture,
        )
    except AppError as exc:
        return _fail(exc.code)
    token, _ = acc.create_session(
        db,
        user,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    response = RedirectResponse(safe_return_to(saved.get("r")), status_code=303)
    response.delete_cookie(STATE_COOKIE, path=STATE_COOKIE_PATH)
    set_session_cookie(response, settings, token, request.app.state.signing_key)
    return response


@router.get("/me", response_model=MeOut)
def me(auth: CurrentAuth, db: SessionDep) -> MeOut:
    """The signed-in user, their organisations and the active one."""
    orgs = acc.user_organizations(db, auth.user.id)
    return MeOut(
        user=UserOut.model_validate(auth.user, from_attributes=True),
        organizations=[
            OrganizationOut(id=o.id, name=o.name, slug=o.slug, role=r, is_demo=o.is_demo)
            for o, r in orgs
        ],
        active_organization_id=auth.org_id,
        role=auth.role,
        csrf_token=auth.csrf_token,
    )


@router.post("/logout", status_code=204)
def logout(auth: CurrentAuth, db: SessionDep, settings: SettingsDep) -> Response:
    acc.revoke_session(db, auth.session)
    response = Response(status_code=204)
    clear_session_cookie(response, settings)
    return response
