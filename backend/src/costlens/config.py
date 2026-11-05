"""Application settings.

All configuration comes from environment variables prefixed with ``COSTLENS_``
(or a ``.env`` file in development). Nothing secret has a default.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_PREFIX = "COSTLENS_"
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

    deployment_mode: DeploymentMode = DeploymentMode.LOCAL
    environment_name: str = "development"
    base_url: str = "http://localhost:8000"
    log_level: str = "INFO"
    # "json" (one JSON object per line), "text", or "auto" (json unless local mode).
    log_format: Literal["auto", "json", "text"] = "auto"
    # Bearer token for /metrics. Without it, /metrics is only served in local mode.
    metrics_token: SecretStr | None = None

    database_url: str = "sqlite:///./data/costlens.db"
    # PostgreSQL: owner/admin connection used only by `costlens migrate`. The runtime
    # `database_url` should be a separate role without superuser/BYPASSRLS so that
    # row-level security applies; migrate grants it data access.
    migration_database_url: str | None = None
    # Escape hatch for setups that cannot create a separate role (not recommended).
    allow_privileged_db_role: bool = False
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
    google_client_secret: SecretStr | None = None
    # Only these email domains may sign in (empty = any). Useful for self-hosted.
    allowed_email_domains: list[str] = Field(default_factory=list)

    # Optional AI summaries.
    anthropic_api_key: SecretStr | None = None
    ai_model: str = DEFAULT_AI_MODEL

    cors_origins: list[str] = Field(default_factory=list)

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
