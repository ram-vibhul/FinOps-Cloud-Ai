"""Housekeeping jobs."""

from __future__ import annotations

from ccradar.core.ratelimit import cleanup_postgres_buckets
from ccradar.jobs.context import job_context
from ccradar.jobs.registry import job


@job("system.cleanup_rate_limits", queue="maintenance", cron="*/10 * * * *")
def cleanup_rate_limits() -> int:
    ctx = job_context()
    if not ctx.settings.is_postgres:
        return 0
    return cleanup_postgres_buckets(ctx.db.engine)


HEARTBEAT_FILE = "/tmp/ccradar-worker-heartbeat"  # noqa: S108 - per-container, not shared


@job("system.heartbeat", queue="maintenance", cron="*/5 * * * *")
def heartbeat() -> bool:
    """Every 5 minutes: check the database, record the time for the container health check
    (``ccradar worker-health``), and call ``CCRADAR_HEARTBEAT_URL`` if set (an Uptime
    Kuma push monitor), so monitoring notices when jobs stop (0.4, D.9)."""
    import time
    from pathlib import Path

    import httpx
    from sqlalchemy import text

    from ccradar.core.notify import outbound

    ctx = job_context()
    with ctx.db.session() as session:
        session.execute(text("SELECT 1"))
    Path(HEARTBEAT_FILE).write_text(str(int(time.time())))
    if not ctx.settings.heartbeat_url:
        return False
    http, _ = outbound()
    try:
        http.get(ctx.settings.heartbeat_url, params={"status": "up", "msg": "OK"}, timeout=10.0)
    except httpx.HTTPError:
        return False  # monitoring alerts on the missed beat
    return True


def worker_healthy(max_age_seconds: int = 900, path: str = HEARTBEAT_FILE) -> bool:
    """True when the heartbeat job ran recently in this container."""
    import time
    from pathlib import Path

    try:
        beat = int(Path(path).read_text().strip())
    except (OSError, ValueError):
        return False
    return time.time() - beat <= max_age_seconds
