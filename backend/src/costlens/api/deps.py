"""FastAPI dependencies shared by routers."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from costlens.accounts.roles import Permission, has_permission
from costlens.auth.web import AuthContext, authenticate, session_cookie_name
from costlens.config import Settings
from costlens.core.errors import AppError, PermissionDeniedError
from costlens.db import Database


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
    return auth


OptionalAuth = Annotated[AuthContext | None, Depends(get_optional_auth)]
CurrentAuth = Annotated[AuthContext, Depends(get_current_auth)]


def require(permission: Permission):  # type: ignore[no-untyped-def]
    """Dependency: signed in, an active organisation, and a role allowing ``permission``."""

    def dependency(auth: CurrentAuth) -> AuthContext:
        if auth.org_id is None or auth.role is None:
            raise AppError("Choose an organisation first.", status=409, code="no_organization")
        if not has_permission(auth.role, permission):
            raise PermissionDeniedError("Your role does not allow this.")
        if permission is not Permission.READ and auth.org_is_demo:
            raise AppError(
                "The demo organisation is read-only. Create your own organisation to make changes.",
                status=403,
                code="demo_read_only",
            )
        return auth

    return Depends(dependency)
