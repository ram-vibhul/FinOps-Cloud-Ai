"""Application factory."""

from __future__ import annotations

import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from costlens import PRODUCT_NAME, __version__
from costlens.api import auth, system
from costlens.config import DeploymentMode, Settings, get_settings
from costlens.core.errors import install_error_handlers
from costlens.core.logging import configure_logging
from costlens.core.metrics import BUILD_INFO
from costlens.core.middleware import RequestContextMiddleware
from costlens.core.spa import mount_spa
from costlens.db import Database
from costlens.db.rls import is_privileged_role
from costlens.jobs import JobRunner, build_runner


def check_database_role(settings: Settings, database: Database) -> None:
    """Refuse to serve customers through a role that would ignore row-level security."""
    if not settings.is_postgres or settings.deployment_mode is DeploymentMode.LOCAL:
        return
    with database.engine.connect() as conn:
        privileged = is_privileged_role(conn)
    if privileged and not settings.allow_privileged_db_role:
        raise RuntimeError(
            "COSTLENS_DATABASE_URL connects as a superuser or BYPASSRLS role, which would "
            "disable tenant isolation. Use a separate application role (see docs) and put "
            "the owner role in COSTLENS_MIGRATION_DATABASE_URL, or set "
            "COSTLENS_ALLOW_PRIVILEGED_DB_ROLE=true to override (not recommended)."
        )
    if privileged:
        logging.getLogger(__name__).warning("database role bypasses row-level security")


def create_app(
    settings: Settings | None = None,
    *,
    db: Database | None = None,
    runner: JobRunner | None = None,
    http_client: httpx.Client | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.effective_log_format)
    BUILD_INFO.set(1, version=__version__, mode=settings.deployment_mode.value)
    database = db or Database(settings.database_url, echo=settings.database_echo)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        check_database_role(settings, database)
        app.state.jobs = runner or build_runner(settings)
        yield
        if http_client is None:
            app.state.http.close()
        database.dispose()

    app = FastAPI(
        title=f"{PRODUCT_NAME} API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    app.state.settings = settings
    app.state.db = database
    # One outbound HTTP client (connection pooling); tests inject a fake transport.
    app.state.http = http_client or httpx.Client(
        timeout=10.0,
        follow_redirects=False,
        headers={"User-Agent": f"{PRODUCT_NAME}/{__version__}"},
    )
    app.state.oidc_clients = {}
    if settings.secret_key is not None:
        app.state.signing_key = settings.secret_key.get_secret_value()
    else:  # local mode only (validated in Settings): sign-in state survives until restart
        app.state.signing_key = secrets.token_urlsafe(32)

    install_error_handlers(app)
    app.add_middleware(
        RequestContextMiddleware, hsts=settings.deployment_mode is not DeploymentMode.LOCAL
    )
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Content-Type", "X-CSRF-Token", "Authorization"],
        )

    app.include_router(system.router)
    app.include_router(auth.router)
    if settings.ui_dist_dir:
        mount_spa(app, settings.ui_dist_dir)
    return app
