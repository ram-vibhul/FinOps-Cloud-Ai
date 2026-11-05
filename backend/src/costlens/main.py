"""Application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from costlens import PRODUCT_NAME, __version__
from costlens.ai.writer import TextWriter, build_writer
from costlens.api import (
    agent as agent_api,
)
from costlens.api import (
    anomalies,
    audit,
    auth,
    changes,
    clusters,
    connections,
    costs,
    demo,
    explorer,
    files,
    inventory,
    invitations,
    organizations,
    privacy,
    quality,
    sso,
    system,
    vtags,
)
from costlens.api import (
    fx as fx_api,
)
from costlens.auth.web import session_cookie_name
from costlens.clouds.aws.client import AwsGateway, Boto3Gateway
from costlens.config import DeploymentMode, Settings, get_settings
from costlens.core.crypto import (
    SecretBox,
    configure_encryption,
    local_dev_key,
    local_dev_signing_key,
)
from costlens.core.csrf import CSRFMiddleware
from costlens.core.email import EmailSender, build_sender, configure_email
from costlens.core.errors import install_error_handlers
from costlens.core.logging import configure_logging
from costlens.core.metrics import BUILD_INFO
from costlens.core.middleware import RequestContextMiddleware
from costlens.core.netguard import Resolver, system_resolver
from costlens.core.ratelimit import (
    MemoryLimiter,
    Policy,
    PostgresLimiter,
    RateLimitMiddleware,
    Rule,
)
from costlens.core.spa import mount_spa
from costlens.core.storage import build_storage
from costlens.db import Database
from costlens.db.rls import is_privileged_role
from costlens.jobs import JobRunner, build_runner
from costlens.jobs.context import JobContext, configure_jobs
from costlens.jobs.costs import use_gateway
from costlens.jobs.fx import use_http


def build_secret_box(settings: Settings) -> SecretBox:
    if settings.encryption_keys is not None:
        return SecretBox.from_setting(settings.encryption_keys.get_secret_value())
    # Only reachable in local mode (Settings requires keys elsewhere).
    return SecretBox([local_dev_key(Path(settings.data_dir))])


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
    resolver: Resolver | None = None,
    email: EmailSender | None = None,
    aws: AwsGateway | None = None,
    ai: TextWriter | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.effective_log_format)
    BUILD_INFO.set(1, version=__version__, mode=settings.deployment_mode.value)
    database = db or Database(settings.database_url, echo=settings.database_echo)
    configure_encryption(build_secret_box(settings))
    configure_email(email or build_sender(settings))
    storage = build_storage(settings.data_dir)
    configure_jobs(JobContext(settings=settings, db=database, storage=storage))

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
    app.state.storage = storage
    # One outbound HTTP client (connection pooling); tests inject a fake transport.
    app.state.http = http_client or httpx.Client(
        timeout=10.0,
        follow_redirects=False,
        headers={"User-Agent": f"{PRODUCT_NAME}/{__version__}"},
    )
    app.state.oidc_clients = {}
    app.state.aws = aws or Boto3Gateway(region=settings.aws_region)
    app.state.ai = ai if ai is not None else build_writer(settings)
    use_gateway(app.state.aws)  # jobs run in this process share it
    use_http(app.state.http)
    app.state.resolver = resolver or system_resolver  # DNS for SSRF checks; faked in tests
    if settings.secret_key is not None:
        app.state.signing_key = settings.secret_key.get_secret_value()
    else:  # local mode only (validated in Settings)
        app.state.signing_key = local_dev_signing_key(Path(settings.data_dir))

    install_error_handlers(app)
    if settings.rate_limit_enabled:
        app.state.rate_limiter = (
            PostgresLimiter(database.engine) if settings.is_postgres else MemoryLimiter()
        )
        app.add_middleware(
            RateLimitMiddleware,
            limiter=app.state.rate_limiter,
            policy=Policy(
                auth=Rule("auth", settings.rate_limit_auth_per_minute),
                anonymous=Rule("anonymous", settings.rate_limit_anonymous_per_minute),
                user=Rule("user", settings.rate_limit_user_per_minute),
                write=Rule("write", settings.rate_limit_write_per_minute),
            ),
            session_cookie=session_cookie_name(settings),
        )
    app.add_middleware(
        CSRFMiddleware,
        signing_key=app.state.signing_key,
        session_cookie=session_cookie_name(settings),
        allowed_origins=[settings.base_url, *settings.cors_origins],
        allow_any_localhost=settings.deployment_mode is DeploymentMode.LOCAL,
    )
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

    for module in (
        system,
        auth,
        sso,
        invitations,
        organizations,
        audit,
        demo,
        privacy,
        connections,
        files,
        costs,
        anomalies,
        explorer,
        changes,
        quality,
        inventory,
        vtags,
        fx_api,
        clusters,
        agent_api,
    ):
        app.include_router(module.router)
    if settings.ui_dist_dir:
        mount_spa(app, settings.ui_dist_dir)
    return app
