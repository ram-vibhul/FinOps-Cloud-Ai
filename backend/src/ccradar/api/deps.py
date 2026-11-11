"""FastAPI dependencies shared by routers."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ccradar.accounts.roles import Permission, has_permission
from ccradar.auth.web import AuthContext, authenticate, session_cookie_name
from ccradar.config import Settings
from ccradar.core.errors import AppError, PermissionDeniedError
from ccradar.db import Database


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_database(request: Request) -> Database:
    return request.app.state.db


def get_session(db: Annotated[Database, Depends(get_database)]) -> Iterator[Session]:
    with db.session() as session:
        yield session


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
DatabaseDep = Annotated[Database, Depends(get_database)]
SessionDep = Annotated[Session, Depends(get_session)]


def get_optional_auth(request: Request, db: SessionDep) -> AuthContext | None:
    auth = authenticate(request, db)
    request.state.auth = auth
    return auth


def get_current_auth(
    request: Request, auth: Annotated[AuthContext | None, Depends(get_optional_auth)]
) -> AuthContext:
    if auth is None:
        headers = None
        settings: Settings = request.app.state.settings
        name = session_cookie_name(settings)
        if name in request.cookies:  # expired or revoked: tell the browser to drop it
            secure = "; Secure" if settings.secure_cookies else ""
            headers = {"Set-Cookie": f"{name}=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax{secure}"}
        raise HTTPException(status_code=401, detail="Sign in to continue.", headers=headers)
    if auth.api_key_id is not None:
        from ccradar.accounts.api_keys import BLOCKED

        if BLOCKED.match(request.url.path):
            raise PermissionDeniedError("API keys cannot do this; sign in instead.")
    if auth.scope:
        from ccradar.accounts.custom_roles import SCOPED_PATHS

        if not SCOPED_PATHS.match(request.url.path):
            raise PermissionDeniedError(
                "Your role is limited to some accounts or teams and cannot open this view."
            )
    return auth


OptionalAuth = Annotated[AuthContext | None, Depends(get_optional_auth)]
CurrentAuth = Annotated[AuthContext, Depends(get_current_auth)]


def require(permission: Permission):  # type: ignore[no-untyped-def]
    """Dependency: signed in, an active organisation, and a role allowing ``permission``."""

    def dependency(auth: CurrentAuth) -> AuthContext:
        if auth.org_id is None or auth.role is None:
            raise AppError("Choose an organisation first.", status=409, code="no_organization")
        allowed = (
            permission.value in auth.permissions
            if auth.permissions is not None
            else has_permission(auth.role, permission)
        )
        if not allowed:
            raise PermissionDeniedError("Your role does not allow this.")
        if permission is not Permission.READ and auth.org_is_demo:
            raise AppError(
                "The demo organisation is read-only. Create your own organisation to make changes.",
                status=403,
                code="demo_read_only",
            )
        return auth

    return Depends(dependency)


def plan_feature(feature: str):  # type: ignore[no-untyped-def]
    """Dependency: the organisation's plan includes ``feature`` (B.1). Used on the routes
    that switch a feature on or add to it; what exists already keeps working."""

    def dependency(auth: CurrentAuth, db: SessionDep, settings: SettingsDep) -> None:
        from ccradar.billing.plans import require_feature
        from ccradar.models import Organization

        if auth.org_id is None:
            return
        org = db.get(Organization, auth.org_id)
        if org is not None:
            require_feature(org, settings, feature)

    return Depends(dependency)
