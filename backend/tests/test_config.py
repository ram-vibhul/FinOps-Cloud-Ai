from __future__ import annotations

import pytest
from pydantic import ValidationError

from costlens.config import DEFAULT_AI_MODEL, DeploymentMode, Settings


def make(**kwargs) -> Settings:
    return Settings(_env_file=None, **kwargs)  # type: ignore[call-arg]


def test_local_defaults() -> None:
    s = make()
    assert s.deployment_mode is DeploymentMode.LOCAL
    assert s.is_sqlite
    assert s.run_jobs_in_process
    assert s.ai_model == DEFAULT_AI_MODEL == "claude-opus-5-5"
    assert not s.ai_enabled


def test_env_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COSTLENS_ENVIRONMENT_NAME", "staging")
    assert make().environment_name == "staging"


def test_production_requires_secrets() -> None:
    with pytest.raises(ValidationError, match="COSTLENS_SECRET_KEY"):
        make(deployment_mode="saas", database_url="postgresql://u@h/db")


def test_production_requires_postgres() -> None:
    with pytest.raises(ValidationError, match="PostgreSQL"):
        make(deployment_mode="self_hosted", secret_key="s", encryption_keys="k")


def test_production_ok_with_postgres() -> None:
    s = make(
        deployment_mode="saas",
        secret_key="s",
        encryption_keys="k",
        database_url="postgresql://u@h/db",
    )
    assert s.is_postgres
    assert not s.run_jobs_in_process


def test_jobs_in_process_override() -> None:
    assert make(jobs_in_process=False).run_jobs_in_process is False
