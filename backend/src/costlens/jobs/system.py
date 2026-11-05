"""Housekeeping jobs."""

from __future__ import annotations

from costlens.core.ratelimit import cleanup_postgres_buckets
from costlens.jobs.context import job_context
from costlens.jobs.registry import job


@job("system.cleanup_rate_limits", queue="maintenance", cron="*/10 * * * *")
def cleanup_rate_limits() -> int:
    ctx = job_context()
    if not ctx.settings.is_postgres:
        return 0
    return cleanup_postgres_buckets(ctx.db.engine)
