"""Application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from costlens import PRODUCT_NAME, __version__
from costlens.api import system
from costlens.config import DeploymentMode, Settings, get_settings
from costlens.core.middleware import SecurityHeadersMiddleware
from costlens.core.spa import mount_spa
from costlens.db import Database
from costlens.jobs import JobRunner, build_runner


def create_app(
    settings: Settings | None = None,
    *,
    db: Database | None = None,
    runner: JobRunner | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level.upper())
    database = db or Database(settings.database_url, echo=settings.database_echo)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.jobs = runner or build_runner(settings)
        yield
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

    app.add_middleware(
        SecurityHeadersMiddleware, hsts=settings.deployment_mode is not DeploymentMode.LOCAL
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
    if settings.ui_dist_dir:
        mount_spa(app, settings.ui_dist_dir)
    return app
