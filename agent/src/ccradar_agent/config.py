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
    url = env.get("CCRADAR_URL", "").rstrip("/")
    if not url.startswith(("https://", "http://")):
        raise ConfigError(
            "CCRADAR_URL must be the address of your CloudCostRadar, e.g. https://..."
        )
    if url.startswith("http://") and env.get("CCRADAR_ALLOW_HTTP") != "true":
        raise ConfigError("CCRADAR_URL must use https (set CCRADAR_ALLOW_HTTP=true to test).")
    token = env.get("CCRADAR_TOKEN", "")
    token_file = env.get("CCRADAR_TOKEN_FILE")
    if not token and token_file:
        token = Path(token_file).read_text().strip()
    if not token.startswith("ckt_"):
        raise ConfigError("CCRADAR_TOKEN (or CCRADAR_TOKEN_FILE) must hold the cluster token.")
    return AgentConfig(url=url, token=token, cluster_name=env.get("CCRADAR_CLUSTER_NAME", ""))
