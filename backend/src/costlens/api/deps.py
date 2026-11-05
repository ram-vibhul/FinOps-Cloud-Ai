"""FastAPI dependencies shared by routers."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from costlens.auth.web import AuthContext, authenticate
from costlens.config import Settings
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
    auth: Annotated[AuthContext | None, Depends(get_optional_auth)],
) -> AuthContext:
    if auth is None:
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    return auth


OptionalAuth = Annotated[AuthContext | None, Depends(get_optional_auth)]
CurrentAuth = Annotated[AuthContext, Depends(get_current_auth)]
