"""Agent settings from environment variables (set by the Helm chart)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class AgentConfig:
    url: str
    token: str
    cluster_name: str
    timeout_seconds: float = 30.0
    chunk_size: int = 1000


def from_env(env: dict[str, str] | None = None) -> AgentConfig:
    env = dict(os.environ if env is None else env)
    url = env.get("COSTLENS_URL", "").rstrip("/")
    if not url.startswith(("https://", "http://")):
        raise ConfigError("COSTLENS_URL must be the address of your CostLens, e.g. https://...")
    if url.startswith("http://") and env.get("COSTLENS_ALLOW_HTTP") != "true":
        raise ConfigError("COSTLENS_URL must use https (set COSTLENS_ALLOW_HTTP=true to test).")
    token = env.get("COSTLENS_TOKEN", "")
    token_file = env.get("COSTLENS_TOKEN_FILE")
    if not token and token_file:
        token = Path(token_file).read_text().strip()
    if not token.startswith("ckt_"):
        raise ConfigError("COSTLENS_TOKEN (or COSTLENS_TOKEN_FILE) must hold the cluster token.")
    return AgentConfig(url=url, token=token, cluster_name=env.get("COSTLENS_CLUSTER_NAME", ""))
