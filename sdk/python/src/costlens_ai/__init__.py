"""Report AI/LLM usage to CostLens.

    from costlens_ai import Usage

    usage = Usage(api_key="clai_...", url="https://costlens.example.com", app="checkout")
    response = anthropic_client.messages.create(...)
    usage.from_anthropic(response, feature="summaries", prompt=prompt_text)
    ...
    usage.close()  # or rely on the automatic flush at exit

What is sent: provider, model, token counts, optional cost, app/team/feature/environment
names, retry and error flags, latency, and a *fingerprint* of the prompt. The fingerprint
is an HMAC-SHA256 of the prompt with a salt that stays in your process, so CostLens can
spot repeated prompts without ever seeing them. Prompt and completion text are never
sent: there is no field for them, and ``prompt=`` is used only to make the fingerprint.

Reporting never raises into your code. Failed batches are retried on the next flush and
dropped after a few attempts; the buffer is bounded.
"""

from __future__ import annotations

import atexit
import hashlib
import hmac
import json
import logging
import re
import secrets
import threading
import urllib.request
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

__all__ = ["Usage", "fingerprint"]
__version__ = "0.1.0"

log = logging.getLogger("costlens_ai")
IDENT = re.compile(r"^[\w.:/@+\-]{1,100}$")
MAX_BUFFER = 10_000
MAX_ATTEMPTS = 3
Transport = Callable[[str, bytes, dict[str, str]], int]


def _clean(value: str | None, limit: int = 100) -> str | None:
    """Make a name safe to send: no spaces or unusual characters (so no sentences)."""
    if value is None:
        return None
    s = re.sub(r"[^\w.:/@+\-]+", "-", str(value).strip())[:limit].strip("-")
    return s if s and IDENT.match(s) else None


def fingerprint(prompt: str, salt: bytes) -> str:
    """Salted fingerprint of a prompt (whitespace-normalised). Safe to send."""
    normal = " ".join(prompt.split())
    return hmac.new(salt, normal.encode(), hashlib.sha256).hexdigest()[:32]


def _urllib_transport(url: str, body: bytes, headers: dict[str, str]) -> int:
    if not url.startswith("https://") and not url.startswith("http://localhost"):
        raise ValueError("CostLens URL must use https")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")  # noqa: S310
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - https checked above
        return int(resp.status)


class Usage:
    def __init__(
        self,
        api_key: str,
        url: str,
        *,
        app: str | None = None,
        team: str | None = None,
        environment: str | None = None,
        salt: bytes | None = None,
        batch_size: int = 100,
        transport: Transport | None = None,
    ) -> None:
        if not api_key.startswith("clai_"):
            raise ValueError("use a CostLens AI usage key (starts with clai_)")
        self._key = api_key
        self._endpoint = url.rstrip("/") + "/api/v1/ingest/ai-usage"
        self._defaults = {
            "app": _clean(app),
            "team": _clean(team),
            "environment": _clean(environment, 40),
        }
        # Keep the same salt across processes (e.g. from an environment variable) to match
        # repeated prompts across them. It must never be sent anywhere.
        self._salt = salt or secrets.token_bytes(32)
        self._batch = max(1, min(batch_size, 1000))
        self._send = transport or _urllib_transport
        self._buffer: list[dict[str, Any]] = []
        self._failures = 0
        self._lock = threading.Lock()
        atexit.register(self.close)

    def record(
        self,
        provider: str,
        model: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        cost_usd: float | None = None,
        feature: str | None = None,
        team: str | None = None,
        app: str | None = None,
        retry: bool = False,
        error: bool = False,
        latency_ms: int | None = None,
        prompt: str | None = None,
        at: datetime | None = None,
    ) -> dict[str, Any]:
        """Queue one event and return exactly what will be sent."""
        event: dict[str, Any] = {
            "at": (at or datetime.now(timezone.utc)).isoformat(),
            "provider": _clean(provider, 40) or "unknown",
            "model": _clean(model) or "unknown",
            "input_tokens": max(0, int(input_tokens)),
            "output_tokens": max(0, int(output_tokens)),
            "cache_read_tokens": max(0, int(cache_read_tokens)),
            "cache_write_tokens": max(0, int(cache_write_tokens)),
            "retry": bool(retry),
            "status": "error" if error else "ok",
        }
        for name, value in (
            ("app", _clean(app) or self._defaults["app"]),
            ("team", _clean(team) or self._defaults["team"]),
            ("feature", _clean(feature)),
            ("environment", self._defaults["environment"]),
        ):
            if value:
                event[name] = value
        if cost_usd is not None:
            event["cost_usd"] = round(max(0.0, float(cost_usd)), 6)
        if latency_ms is not None:
            event["latency_ms"] = max(0, int(latency_ms))
        if prompt:
            event["fingerprint"] = fingerprint(prompt, self._salt)
        with self._lock:
            if len(self._buffer) >= MAX_BUFFER:
                self._buffer.pop(0)
            self._buffer.append(event)
            full = len(self._buffer) >= self._batch
        if full:
            self.flush()
        return event

    def from_anthropic(self, response: Any, **kw: Any) -> dict[str, Any]:
        """Record an Anthropic Messages API response (reads ``response.usage``)."""
        u = getattr(response, "usage", None)
        return self.record(
            "anthropic",
            str(getattr(response, "model", "unknown")),
            input_tokens=getattr(u, "input_tokens", 0) or 0,
            output_tokens=getattr(u, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
            **kw,
        )

    def from_openai(self, response: Any, **kw: Any) -> dict[str, Any]:
        """Record an OpenAI-style chat completion (reads ``response.usage``)."""
        u = getattr(response, "usage", None)
        details = getattr(u, "prompt_tokens_details", None)
        cached = getattr(details, "cached_tokens", 0) or 0
        prompt_tokens = getattr(u, "prompt_tokens", 0) or 0
        return self.record(
            kw.pop("provider", "openai"),
            str(getattr(response, "model", "unknown")),
            input_tokens=max(0, prompt_tokens - cached),
            output_tokens=getattr(u, "completion_tokens", 0) or 0,
            cache_read_tokens=cached,
            **kw,
        )

    def flush(self) -> bool:
        """Send what is queued. Returns True when the buffer is empty afterwards."""
        with self._lock:
            batch, self._buffer = self._buffer[:1000], self._buffer[1000:]
        if not batch:
            return True
        body = json.dumps({"events": batch}, separators=(",", ":")).encode()
        headers = {
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
            "User-Agent": f"costlens-ai-python/{__version__}",
        }
        try:
            status = self._send(self._endpoint, body, headers)
            ok = 200 <= status < 300
        except Exception as exc:  # never break the caller
            log.warning("CostLens usage report failed: %s", type(exc).__name__)
            ok, status = False, 0
        if ok:
            self._failures = 0
        elif status in (400, 401, 403, 422):
            log.warning("CostLens rejected a usage batch (%s); dropping it", status)
        else:
            self._failures += 1
            if self._failures < MAX_ATTEMPTS:
                with self._lock:
                    self._buffer[:0] = batch  # retry on the next flush
            else:
                log.warning("CostLens usage batch dropped after %s attempts", MAX_ATTEMPTS)
                self._failures = 0
        with self._lock:
            return not self._buffer

    def close(self) -> None:
        for _ in range(MAX_ATTEMPTS):
            if self.flush():
                return
