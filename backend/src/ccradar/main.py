"""Application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ccradar import PRODUCT_NAME, __version__
from ccradar.ai.writer import TextWriter, build_writer
from ccradar.api import (
    agent as agent_api,
)
from ccradar.api import (
    ai_usage,
    allocation,
    anomalies,
    api_keys,
    audit,
    auth,
    aws_audit,
    budgets,
    changes,
    clusters,
    commitments,
    connections,
    costs,
    custom_roles,
    demo,
    elevation,
    estimates,
    explorer,
    files,
    findings,
    fixes,
    flow_logs,
    governance,
    inbox,
    inventory,
    invitations,
    k8s_costs,
    k8s_investigations,
    k8s_metrics,
    k8s_tasks,
    k8s_usage,
    mcp,
    notifications,
    organizations,
    partners,
    payment_webhooks,
    plans,
    privacy,
    quality,
    reliability,
    reports,
    roi,
    scim,
    setup,
    sso,
    statements,
    subscriptions,
    system,
    teams,
    unit_metrics,
    vtags,
    webhooks_out,
    work,
)
from ccradar.api import (
    fx as fx_api,
)
from ccradar.auth.web import session_cookie_name
from ccradar.clouds.aws.client import AwsGateway, Boto3Gateway
from ccradar.config import DeploymentMode, Settings, get_settings
from ccradar.core.crypto import (
    SecretBox,
    configure_encryption,
    local_dev_key,
    local_dev_signing_key,
)
from ccradar.core.csrf import CSRFMiddleware
from ccradar.core.email import EmailSender, build_sender, configure_email
from ccradar.core.errors import install_error_handlers
from ccradar.core.logging import configure_logging
from ccradar.core.metrics import BUILD_INFO
from ccradar.core.middleware import RequestContextMiddleware
from ccradar.core.netguard import Resolver, system_resolver
from ccradar.core.notify import configure as configure_notify
from ccradar.core.ratelimit import (
    MemoryLimiter,
    Policy,
    PostgresLimiter,
    RateLimitMiddleware,
    Rule,
)
from ccradar.core.spa import mount_spa
from ccradar.core.storage import build_storage
from ccradar.db import Database
from ccradar.db.rls import is_privileged_role
from ccradar.jobs import JobRunner, build_runner
from ccradar.jobs.context import JobContext, configure_jobs
from ccradar.jobs.costs import use_gateway
from ccradar.jobs.fx import use_http


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
            "CCRADAR_DATABASE_URL connects as a superuser or BYPASSRLS role, which would "
            "disable tenant isolation. Use a separate application role (see docs) and put "
            "the owner role in CCRADAR_MIGRATION_DATABASE_URL, or set "
            "CCRADAR_ALLOW_PRIVILEGED_DB_ROLE=true to override (not recommended)."
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
    configure_notify(app.state.http, app.state.resolver)
    from ccradar.core.error_tracking import configure as configure_error_tracking

    configure_error_tracking(settings)
    if settings.secret_key is not None:
        app.state.signing_key = settings.secret_key.get_secret_value()
    else:  # local mode only (validated in Settings)
        app.state.signing_key = local_dev_signing_key(Path(settings.data_dir))

    install_error_handlers(app)
    app.add_exception_handler(scim.ScimError, scim.scim_error_response)
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
        work,
        estimates,
        webhooks_out,
        ai_usage,
        api_keys,
        teams,
        scim,
        elevation,
        partners,
        fixes,
        mcp,
        custom_roles,
        setup,
        inbox,
        plans,
        subscriptions,
        payment_webhooks,
        fx_api,
        clusters,
        agent_api,
        k8s_costs,
        k8s_usage,
        k8s_investigations,
        k8s_metrics,
        findings,
        commitments,
        reliability,
        aws_audit,
        flow_logs,
        roi,
        budgets,
        governance,
        allocation,
        statements,
        reports,
        unit_metrics,
        notifications,
        k8s_tasks,
    ):
        app.include_router(module.router)
    if settings.ui_dist_dir:
        mount_spa(app, settings.ui_dist_dir)
    _document_api(app)
    return app


API_GUIDE = """
Every endpoint under `/api/v1` that the web app uses is also available to scripts.

**Authentication.** Create an API key under *Settings → API keys* and send it as
`Authorization: Bearer clk_…`. A key acts for the organisation it was created in, with
the lower of its scope (`read`, `write`, `admin`) and its creator's current role, and stops
working when revoked, expired, or when its creator leaves the organisation. Keys cannot
sign in, manage members, SSO, exports or other keys.

**Limits.** Each key has its own per-minute limits (HTTP 429 with `Retry-After`).

**Errors** are RFC 9457 problem documents with a stable `code`.
"""


def _document_api(app: FastAPI) -> None:
    from fastapi.openapi.utils import get_openapi

    def openapi() -> dict:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title, version=app.version, description=API_GUIDE, routes=app.routes
        )
        schema.setdefault("components", {})["securitySchemes"] = {
            "apiKey": {
                "type": "http",
                "scheme": "bearer",
                "description": "An organisation API key (clk_…).",
            }
        }
        schema["security"] = [{"apiKey": []}]
        app.openapi_schema = schema
        return schema

    app.openapi = openapi  # type: ignore[method-assign]
