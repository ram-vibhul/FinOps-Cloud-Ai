"""Application settings.

All configuration comes from environment variables prefixed with ``CCRADAR_``
(or a ``.env`` file in development). Nothing secret has a default.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_PREFIX = "CCRADAR_"
DEFAULT_AI_MODEL = "claude-opus-5-5"


class DeploymentMode(StrEnum):
    """How this instance is run. The same build serves all three."""

    LOCAL = "local"  # developer laptop / quick trial
    SAAS = "saas"  # hosted by Yugiverse, billing enabled
    SELF_HOSTED = "self_hosted"  # run by the customer, licence key enabled


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # How this instance runs: local (laptop, trial), saas (hosted by Yugiverse,
    # billing), self_hosted (licence key).
    deployment_mode: DeploymentMode = DeploymentMode.LOCAL
    # A label for logs and error reports, e.g. production or staging.
    environment_name: str = "development"
    # Free trial for new SaaS organisations, in days (0 = none). The length is a
    # placeholder until plans and prices are decided (tracker Q2).
    trial_days: int = Field(default=14, ge=0, le=365)
    # Plan catalogue (B.1): a JSON file of plans, features and limits. Unset = the
    # packaged placeholder plans (tracker Q2).
    plans_file: str | None = None
    # Failed payments (B.16): days a past-due subscription keeps its plan before it moves
    # to the free plan. The payment provider retries the charge meanwhile.
    payment_grace_days: int = Field(default=14, ge=1, le=60)
    # Error tracking (D.9): a Sentry-protocol DSN, e.g. of a self-hosted GlitchTip.
    # Unset = off. Reports are scrubbed of personal data and secrets.
    error_tracking_dsn: SecretStr | None = None
    # Called every 5 minutes by the worker (an Uptime Kuma "push" URL), so monitoring
    # notices when background jobs stop. Unset = no heartbeat.
    heartbeat_url: str | None = None
    # Payment webhooks (B.5): the signing secrets from the Razorpay and Stripe dashboards.
    # Unset = that provider's webhook endpoint answers 404.
    razorpay_webhook_secret: SecretStr | None = None
    # Stripe webhook signing secret (whsec_…), see above.
    stripe_webhook_secret: SecretStr | None = None
    # Self-hosted licence (B.6): the key itself, or a file holding it. It can also be
    # installed with `ccradar licence install` or by a platform admin in the UI.
    licence_key: SecretStr | None = None
    # A file holding the self-hosted licence key, see above.
    licence_file: str | None = None
    # The public address of this instance; used in links, emails and sign-in redirects.
    base_url: str = "http://localhost:8000"
    # DEBUG, INFO, WARNING or ERROR.
    log_level: str = "INFO"
    # "json" (one JSON object per line), "text", or "auto" (json unless local mode).
    log_format: Literal["auto", "json", "text"] = "auto"
    # Bearer token for /metrics. Without it, /metrics is only served in local mode.
    metrics_token: SecretStr | None = None

    # SQLAlchemy URL: SQLite for local runs, PostgreSQL 17 (postgresql://…) otherwise.
    database_url: str = "sqlite:///./data/ccradar.db"
    # Local files (SQLite database, local-mode encryption key, uploads in local mode).
    data_dir: str = "./data"
    # PostgreSQL: owner/admin connection used only by `ccradar migrate`. The runtime
    # `database_url` should be a separate role without superuser/BYPASSRLS so that
    # row-level security applies; migrate grants it data access.
    migration_database_url: str | None = None
    # Escape hatch for setups that cannot create a separate role (not recommended).
    allow_privileged_db_role: bool = False
    # Log every SQL statement (debugging only).
    database_echo: bool = False

    # Signs sessions and CSRF tokens. Required outside local mode.
    secret_key: SecretStr | None = None
    # Comma-separated Fernet keys (newest first) for secrets stored at rest.
    encryption_keys: SecretStr | None = None

    # Directory holding the built web UI (``frontend/dist``); served at ``/``.
    ui_dist_dir: Path | None = None

    # Run background jobs inside the web process. Defaults to True on SQLite.
    jobs_in_process: bool | None = None

    # Sign in with Google (OIDC). Create an OAuth client of type "Web application";
    # redirect URI: <base_url>/api/v1/auth/callback/google
    google_client_id: str | None = None
    # The Google OAuth client's secret.
    google_client_secret: SecretStr | None = None
    # More instance-wide providers, JSON list, e.g.
    # [{"id":"entra","kind":"entra","display_name":"Microsoft","tenant":"organizations",
    #   "client_id":"...","client_secret":"..."},
    #  {"id":"okta","kind":"okta","display_name":"Okta","domain":"acme.okta.com", ...}]
    oidc_providers: SecretStr | None = None
    # Development/trial only: sign in with just an email address, no identity provider.
    # Refused unless deployment mode is local.
    local_login: bool = False
    # Only these email domains may sign in (empty = any). Useful for self-hosted.
    allowed_email_domains: list[str] = Field(default_factory=list)

    # Outgoing email over SMTP (any provider). Without a host, emails are logged.
    smtp_host: str | None = None
    # SMTP server port.
    smtp_port: int = 587
    # SMTP user name, if the server needs one.
    smtp_username: str | None = None
    # SMTP password.
    smtp_password: SecretStr | None = None
    # starttls, ssl or none.
    smtp_security: Literal["starttls", "ssl", "none"] = "starttls"
    # The From address of emails CloudCostRadar sends.
    email_from: str = "CloudCostRadar <no-reply@localhost>"

    # AWS: the principal (IAM role/user ARN) customers trust in their read-only role.
    # SaaS: Yugiverse's collector role. Self-hosted: the role your CloudCostRadar runs as.
    aws_principal_arn: str | None = None
    # Region for AWS API calls that need one (Cost Explorer, STS).
    aws_region: str = "us-east-1"

    # Where customers install the Kubernetes agent chart from (shown in install commands).
    # Final registry depends on the GitHub organisation (tracker Q9).
    agent_chart: str = "oci://ghcr.io/yugiverse/charts/cloudcostradar-agent"

    # Optional AI summaries.
    anthropic_api_key: SecretStr | None = None
    # Claude model used for plain-English explanations and questions.
    ai_model: str = DEFAULT_AI_MODEL

    # Extra browser origins allowed to call the API (the UI itself needs none).
    cors_origins: list[str] = Field(default_factory=list)

    # Exchange rates: "ecb" (free European Central Bank reference rates, fetched daily)
    # or "none" (offline installs; costs then show per currency until rates exist).
    fx_provider: Literal["ecb", "none"] = "ecb"

    # Partner sign-up (8.13): "approval" (Yugiverse approves each partner), "self_serve"
    # (owners become partners at once) or "off".
    partner_signup: Literal["approval", "self_serve", "off"] = "approval"

    # Verified email domains (8.2) are checked with DNS-over-HTTPS (JSON API). Point this
    # at your own resolver on private networks.
    dns_over_https_url: str = "https://cloudflare-dns.com/dns-query"

    # Demo organisation with sample data that anyone signed in can explore read-only.
    demo_enabled: bool = True

    # Rate limits (requests per minute). Backend: PostgreSQL when used, else memory.
    rate_limit_enabled: bool = True
    # Sign-in attempts per minute per IP address.
    rate_limit_auth_per_minute: int = 20
    # Requests per minute per IP address without a session.
    rate_limit_anonymous_per_minute: int = 120
    # Requests per minute per signed-in user or API key.
    rate_limit_user_per_minute: int = 600
    # Changes (POST, PUT, PATCH, DELETE) per minute per user or API key.
    rate_limit_write_per_minute: int = 120

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith(("postgresql", "postgres"))

    @property
    def secure_cookies(self) -> bool:
        return self.base_url.startswith("https://")

    @property
    def run_jobs_in_process(self) -> bool:
        if self.jobs_in_process is not None:
            return self.jobs_in_process
        return self.is_sqlite

    @property
    def effective_log_format(self) -> str:
        if self.log_format != "auto":
            return self.log_format
        return "text" if self.deployment_mode is DeploymentMode.LOCAL else "json"

    @property
    def ai_enabled(self) -> bool:
        return self.anthropic_api_key is not None

    @model_validator(mode="after")
    def _check_production_safety(self) -> Self:
        if self.local_login and self.deployment_mode is not DeploymentMode.LOCAL:
            raise ValueError("CCRADAR_LOCAL_LOGIN is only allowed in local mode")
        if self.deployment_mode is not DeploymentMode.LOCAL:
            missing = [
                name for name in ("secret_key", "encryption_keys") if getattr(self, name) is None
            ]
            if missing:
                names = ", ".join(ENV_PREFIX + m.upper() for m in missing)
                raise ValueError(f"{names} must be set when deployment mode is not local")
            if not self.is_postgres:
                raise ValueError("SaaS and self-hosted modes require PostgreSQL")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
