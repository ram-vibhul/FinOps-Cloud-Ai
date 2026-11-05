"""Talk to CostLens: hello, snapshots in chunks, events. Retries brief outages."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from itertools import islice
from typing import Any

import httpx

from costlens_agent import __version__
from costlens_agent.config import AgentConfig


class TokenRejected(RuntimeError):
    """The cluster token was revoked or rotated: stop instead of retrying forever."""


class CostLensApi:
    def __init__(
        self,
        config: AgentConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        http: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        attempts: int = 4,
    ) -> None:
        self.config = config
        self.sleep = sleep
        self.attempts = attempts
        headers = {
            "Authorization": f"Bearer {config.token}",
            "User-Agent": f"costlens-agent/{__version__}",
        }
        if http is not None:  # tests: an in-process client for the CostLens app
            http.headers.update(headers)
            self.http = http
        else:
            self.http = httpx.Client(
                base_url=config.url,
                headers=headers,
                timeout=config.timeout_seconds,
                transport=transport,
            )

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        delay = 1.0
        for attempt in range(self.attempts):
            try:
                response = self.http.post(f"/api/v1/agent/{path}", json=body)
            except httpx.TransportError:
                if attempt == self.attempts - 1:
                    raise
            else:
                if response.status_code == 401:
                    raise TokenRejected("CostLens rejected the cluster token (revoked or rotated).")
                if response.status_code < 500 and response.status_code != 429:
                    response.raise_for_status()
                    return response.json()
                if attempt == self.attempts - 1:
                    response.raise_for_status()
            self.sleep(delay)
            delay = min(delay * 2, 30)
        raise RuntimeError("unreachable")

    def hello(self, kubernetes_version: str | None, provider: str | None) -> dict[str, Any]:
        return self._post(
            "hello",
            {
                "agent_version": __version__,
                "kubernetes_version": kubernetes_version,
                "provider": provider,
            },
        )

    def snapshot(self, kind: str, snapshot_id: str, objects: Iterable[dict[str, Any]]) -> int:
        """Send every object of ``kind`` in chunks; the last chunk is marked final (an
        empty kind still sends one final chunk so deletions are noticed)."""
        it = iter(objects)
        chunk = list(islice(it, self.config.chunk_size))
        sent = 0
        while True:
            following = list(islice(it, self.config.chunk_size))
            self._post(
                "snapshot",
                {
                    "kind": kind,
                    "snapshot_id": snapshot_id,
                    "final": not following,
                    "objects": chunk,
                },
            )
            sent += len(chunk)
            if not following:
                return sent
            chunk = following

    def events(self, events: list[dict[str, Any]]) -> int:
        for i in range(0, len(events), self.config.chunk_size):
            self._post("events", {"events": events[i : i + self.config.chunk_size]})
        return len(events)
