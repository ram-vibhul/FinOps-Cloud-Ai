"""Cross-site request forgery protection for cookie-authenticated requests.

* The token is ``HMAC(signing key, session token)``: bound to the session, no
  server-side state, useless to an attacker without the session cookie.
* The browser gets it in a readable ``costlens_csrf`` cookie (and from ``/me``) and
  must send it back in ``X-CSRF-Token`` on every unsafe request.
* Unsafe requests must also not come from another site: a foreign ``Origin`` or
  ``Sec-Fetch-Site: cross-site`` is rejected even with a valid token.

Requests without the session cookie (API keys, webhooks, SCIM) are not
cookie-authenticated and are left to their own authentication.
"""

from __future__ import annotations

import hashlib
import hmac
from urllib.parse import urlsplit

from starlette.types import ASGIApp, Receive, Scope, Send

from costlens.core.errors import problem

CSRF_COOKIE = "costlens_csrf"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
# Sign-in starts a new session, so it cannot need the old session's token (a stale
# cookie would otherwise lock people out); the cross-site origin checks still apply.
TOKEN_EXEMPT_PATHS = frozenset({"/api/v1/auth/local"})


def csrf_token(signing_key: str, session_token: str) -> str:
    return hmac.new(
        signing_key.encode(), b"csrf:" + session_token.encode(), hashlib.sha256
    ).hexdigest()


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".lower()


class CSRFMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        signing_key: str,
        session_cookie: str,
        allowed_origins: list[str],
        allow_any_localhost: bool = False,
    ) -> None:
        self.app = app
        self.signing_key = signing_key
        self.session_cookie = session_cookie
        self.allowed = {_origin(o) for o in allowed_origins}
        # Local development: the Vite dev server and API run on different ports.
        self.allow_any_localhost = allow_any_localhost

    def _origin_allowed(self, origin: str) -> bool:
        if _origin(origin) in self.allowed:
            return True
        host = urlsplit(origin).hostname or ""
        return self.allow_any_localhost and host in ("localhost", "127.0.0.1", "::1")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in SAFE_METHODS:
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        session = _cookie(headers.get("cookie", ""), self.session_cookie)
        if session is None:  # not cookie-authenticated
            await self.app(scope, receive, send)
            return
        reason = self._reject_reason(
            headers, session, check_token=scope["path"] not in TOKEN_EXEMPT_PATHS
        )
        if reason:
            response = problem(403, detail=reason, code="csrf_failed")
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)

    def _reject_reason(
        self, headers: dict[str, str], session: str, *, check_token: bool = True
    ) -> str | None:
        if headers.get("sec-fetch-site") == "cross-site":
            return "Cross-site request blocked."
        origin = headers.get("origin")
        if origin and origin != "null" and not self._origin_allowed(origin):
            return "Request from another site blocked."
        if origin == "null":
            return "Request from an opaque origin blocked."
        if not check_token:
            return None
        sent = headers.get(CSRF_HEADER, "")
        if not sent or not hmac.compare_digest(sent, csrf_token(self.signing_key, session)):
            return "Missing or invalid CSRF token. Reload the page and try again."
        return None


def _cookie(header: str, name: str) -> str | None:
    for part in header.split(";"):
        key, _, value = part.strip().partition("=")
        if key == name and value:
            return value
    return None
