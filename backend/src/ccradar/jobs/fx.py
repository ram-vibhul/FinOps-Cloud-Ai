"""Daily exchange-rate refresh."""

from __future__ import annotations

import httpx

from ccradar.costs import fx
from ccradar.db.base import utcnow
from ccradar.jobs.context import job_context
from ccradar.jobs.registry import job

_http: httpx.Client | None = None


def use_http(client: httpx.Client) -> None:
    """Jobs in the web process reuse its HTTP client (tests inject a fake transport)."""
    global _http  # noqa: PLW0603
    _http = client


@job("fx.refresh", queue="maintenance", cron="20 16 * * *")  # ECB publishes ~16:00 CET
def refresh_rates() -> int:
    ctx = job_context()
    client = _http or httpx.Client(timeout=30.0)
    with ctx.db.session() as session:
        return fx.refresh(session, client, today=utcnow().date(), provider=ctx.settings.fx_provider)
