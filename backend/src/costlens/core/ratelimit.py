"""Request rate limiting.

Fixed one-minute windows per (rule, identity). Identities: the session cookie
(hashed, no database lookup) for signed-in users, otherwise the client IP (set
from X-Forwarded-For by Uvicorn only for trusted proxies), and each organisation API key
(8.1) on its own.

Backends: in memory (SQLite / single process) or a PostgreSQL table shared by
every web worker and container (one upsert per counted request).
"""

from __future__ import annotations

import hashlib
import math
import threading
import time
from dataclasses import dataclass
from typing import Protocol

import anyio
from sqlalchemy import Engine, text
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from costlens.core.errors import problem
from costlens.core.metrics import REGISTRY, Counter

RATE_LIMITED = REGISTRY.register(
    Counter("costlens_rate_limited_total", "Requests rejected by rate limits.", ("rule",))
)
EXEMPT_PREFIXES = ("/healthz", "/readyz", "/metrics", "/assets/")
UNSAFE = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@dataclass(frozen=True)
class Rule:
    name: str
    limit: int
    window: int = 60  # seconds


@dataclass(frozen=True)
class Hit:
    allowed: bool
    limit: int
    remaining: int
    reset_after: int  # seconds until the window resets


class Limiter(Protocol):
    def hit(self, key: str, rule: Rule, now: float | None = None) -> Hit: ...


def _window(rule: Rule, now: float) -> tuple[int, int]:
    start = int(now // rule.window * rule.window)
    return start, max(1, math.ceil(start + rule.window - now))


class MemoryLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: dict[str, tuple[int, int]] = {}

    def hit(self, key: str, rule: Rule, now: float | None = None) -> Hit:
        now = time.time() if now is None else now
        start, reset = _window(rule, now)
        bucket = f"{rule.name}:{key}"
        with self._lock:
            if len(self._counts) > 100_000:
                self._counts = {k: v for k, v in self._counts.items() if v[0] >= start}
            window_start, count = self._counts.get(bucket, (start, 0))
            count = count + 1 if window_start == start else 1
            self._counts[bucket] = (start, count)
        return Hit(count <= rule.limit, rule.limit, max(0, rule.limit - count), reset)


class PostgresLimiter:
    """Atomic counter per bucket and window via INSERT ... ON CONFLICT."""

    SQL = text(
        """
        INSERT INTO rate_limit_buckets (bucket, window_start, count)
        VALUES (:bucket, :start, 1)
        ON CONFLICT (bucket) DO UPDATE SET
            count = CASE WHEN rate_limit_buckets.window_start = EXCLUDED.window_start
                         THEN rate_limit_buckets.count + 1 ELSE 1 END,
            window_start = EXCLUDED.window_start
        RETURNING count
        """
    )

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def hit(self, key: str, rule: Rule, now: float | None = None) -> Hit:
        now = time.time() if now is None else now
        start, reset = _window(rule, now)
        with self.engine.begin() as conn:
            count = int(
                conn.execute(self.SQL, {"bucket": f"{rule.name}:{key}", "start": start}).scalar()
                or 0
            )
        return Hit(count <= rule.limit, rule.limit, max(0, rule.limit - count), reset)


def cleanup_postgres_buckets(engine: Engine, older_than: float = 3600) -> int:
    with engine.begin() as conn:
        result = conn.execute(
            text("DELETE FROM rate_limit_buckets WHERE window_start < :cutoff"),
            {"cutoff": int(time.time() - older_than)},
        )
    return result.rowcount or 0  # type: ignore[attr-defined]


@dataclass(frozen=True)
class Policy:
    auth: Rule  # sign-in endpoints, per IP
    anonymous: Rule  # any API call without a session, per IP
    user: Rule  # any API call with a session
    write: Rule  # unsafe API calls with a session


def _cookie(header: str, name: str) -> str | None:
    for part in header.split(";"):
        k, _, v = part.strip().partition("=")
        if k == name and v:
            return v
    return None


class RateLimitMiddleware:
    def __init__(
        self, app: ASGIApp, *, limiter: Limiter, policy: Policy, session_cookie: str
    ) -> None:
        self.app = app
        self.limiter = limiter
        self.policy = policy
        self.session_cookie = session_cookie

    def rules_for(self, scope: Scope) -> list[tuple[Rule, str]]:
        path: str = scope["path"]
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        client = scope.get("client")
        ip = client[0] if client else "unknown"
        session = _cookie(headers.get("cookie", ""), self.session_cookie)
        rules: list[tuple[Rule, str]] = []
        if path.startswith("/api/v1/auth/login") or path.startswith("/api/v1/auth/callback"):
            rules.append((self.policy.auth, f"ip:{ip}"))
        bearer = headers.get("authorization", "")
        if not session and bearer.startswith("Bearer clk_"):
            session = bearer  # an API key counts like a session, under its own identity
        if session:
            who = "s:" + hashlib.sha256(session.encode()).hexdigest()[:32]
            rules.append((self.policy.user, who))
            if scope["method"] in UNSAFE:
                rules.append((self.policy.write, who))
        else:
            rules.append((self.policy.anonymous, f"ip:{ip}"))
        return rules

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if (
            scope["type"] != "http"
            or not path.startswith("/api/")
            or path.startswith(EXEMPT_PREFIXES)
        ):
            await self.app(scope, receive, send)
            return
        tightest: Hit | None = None
        for rule, key in self.rules_for(scope):
            hit = await anyio.to_thread.run_sync(self.limiter.hit, key, rule)
            if not hit.allowed:
                RATE_LIMITED.inc(rule=rule.name)
                response = problem(
                    429,
                    detail="Too many requests. Please slow down and try again shortly.",
                    code="rate_limited",
                    headers={"Retry-After": str(hit.reset_after), **_headers(hit)},
                )
                await response(scope, receive, send)
                return
            if tightest is None or hit.remaining < tightest.remaining:
                tightest = hit

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start" and tightest is not None:
                extra = [(k.lower().encode(), v.encode()) for k, v in _headers(tightest).items()]
                message["headers"] = list(message.get("headers", [])) + extra
            await send(message)

        await self.app(scope, receive, send_with_headers)


def _headers(hit: Hit) -> dict[str, str]:
    return {
        "RateLimit-Limit": str(hit.limit),
        "RateLimit-Remaining": str(hit.remaining),
        "RateLimit-Reset": str(hit.reset_after),
    }
