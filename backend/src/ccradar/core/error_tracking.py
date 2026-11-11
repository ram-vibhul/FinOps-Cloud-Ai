"""Error tracking (D.9): unhandled errors go to a Sentry-protocol server (self-hosted
GlitchTip by default) when ``CCRADAR_ERROR_TRACKING_DSN`` is set. Off otherwise.

Nothing personal leaves the installation: no request bodies, cookies, headers, query
strings, user details, IP addresses, local variables or source lines, and every message, exception
text and breadcrumb is passed through the same redaction as the logs (secrets, emails,
phone numbers, IP addresses). No performance tracing."""

from __future__ import annotations

import logging
from typing import Any

from ccradar import __version__
from ccradar.config import Settings
from ccradar.core.redact import redact

log = logging.getLogger(__name__)
KEEP_REQUEST = ("method", "url")  # the URL without its query string


def _clean(text: Any) -> Any:
    return redact(text, ips=True) if isinstance(text, str) else text


def scrub(event: dict[str, Any], _hint: dict[str, Any] | None = None) -> dict[str, Any]:
    """``before_send``/``before_breadcrumb`` hook: keep what helps fix the bug, drop the rest."""
    request = event.get("request")
    if isinstance(request, dict):
        kept = {k: request[k] for k in KEEP_REQUEST if k in request}
        if isinstance(kept.get("url"), str):
            kept["url"] = kept["url"].split("?", 1)[0]
        event["request"] = kept
    event.pop("user", None)
    event.pop("server_name", None)
    for key in ("message", "logentry"):
        value = event.get(key)
        if isinstance(value, str):
            event[key] = _clean(value)
        elif isinstance(value, dict):
            for part in ("message", "formatted"):
                if part in value:
                    value[part] = _clean(value[part])
            value.pop("params", None)
    for exc in (event.get("exception") or {}).get("values", []) or []:
        exc["value"] = _clean(exc.get("value"))
        for frame in (exc.get("stacktrace") or {}).get("frames", []) or []:
            for key in ("vars", "pre_context", "context_line", "post_context"):
                frame.pop(key, None)
    crumbs = event.get("breadcrumbs")
    values = crumbs.get("values", []) if isinstance(crumbs, dict) else crumbs or []
    for crumb in values:
        crumb["message"] = _clean(crumb.get("message"))
        crumb.pop("data", None)
    event.pop("extra", None)
    return event


def scrub_breadcrumb(crumb: dict[str, Any], _hint: dict[str, Any] | None = None) -> dict[str, Any]:
    crumb["message"] = _clean(crumb.get("message"))
    crumb.pop("data", None)
    return crumb


def configure(settings: Settings) -> bool:
    """Start error tracking if a DSN is set. Returns whether it is on."""
    if settings.error_tracking_dsn is None:
        return False
    import sentry_sdk

    sentry_sdk.init(
        dsn=settings.error_tracking_dsn.get_secret_value(),
        environment=settings.environment_name,
        release=f"ccradar@{__version__}",
        # Personal data is off by default in the SDK; the scrubber below enforces it.
        include_local_variables=False,
        include_source_context=False,
        max_request_body_size="never",
        traces_sample_rate=0.0,
        before_send=scrub,  # type: ignore[arg-type]
        before_breadcrumb=scrub_breadcrumb,  # type: ignore[arg-type]
    )
    log.info("error tracking is on (%s)", settings.environment_name)
    return True
