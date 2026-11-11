"""HTTP middleware: request context, access log, metrics, security headers."""

from __future__ import annotations

import logging
import secrets
import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from ccradar.core.errors import internal_error_response
from ccradar.core.logging import request_id_var
from ccradar.core.metrics import HTTP_DURATION, HTTP_REQUESTS

REQUEST_ID_HEADER = "X-Request-ID"
QUIET_PATHS = frozenset({"/healthz", "/readyz", "/metrics"})

access_log = logging.getLogger("ccradar.access")
error_log = logging.getLogger("ccradar.errors")

CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data:",
        "font-src 'self' data:",
        "connect-src 'self'",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "form-action 'self'",
    ]
)


def _request_id(incoming: str) -> str:
    if 0 < len(incoming) <= 64 and incoming.isascii() and incoming.isprintable():
        return incoming
    return secrets.token_hex(8)


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str):
        return path
    return "unmatched"


class RequestContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, hsts: bool) -> None:  # type: ignore[no-untyped-def]
        super().__init__(app)
        self.hsts = hsts

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = _request_id(request.headers.get(REQUEST_ID_HEADER, ""))
        request.state.request_id = request_id
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        try:
            try:
                response = await call_next(request)
            except Exception:
                error_log.exception(
                    "unhandled error", extra={"method": request.method, "path": request.url.path}
                )
                response = internal_error_response()
            elapsed = time.perf_counter() - started
            route = _route_template(request)
            HTTP_REQUESTS.inc(method=request.method, route=route, status=str(response.status_code))
            HTTP_DURATION.observe(elapsed, method=request.method, route=route)
            access_log.log(
                logging.DEBUG if request.url.path in QUIET_PATHS else logging.INFO,
                "%s %s %s",
                request.method,
                request.url.path,
                response.status_code,
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "route": route,
                    "status": response.status_code,
                    "duration_ms": round(elapsed * 1000, 1),
                },
            )
            self._security_headers(response, request_id)
            return response
        finally:
            request_id_var.reset(token)

    def _security_headers(self, response: Response, request_id: str) -> None:
        headers = response.headers
        headers[REQUEST_ID_HEADER] = request_id
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        if self.hsts:
            headers.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains")
