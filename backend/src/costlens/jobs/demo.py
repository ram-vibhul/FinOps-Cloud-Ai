"""Nightly demo reset so every visitor sees fresh, current-looking data."""

from __future__ import annotations

from costlens.demo.seed import reset_demo
from costlens.jobs.context import job_context
from costlens.jobs.registry import job


@job("demo.reset", queue="maintenance", cron="17 3 * * *")
def reset_demo_job() -> None:
    ctx = job_context()
    if not ctx.settings.demo_enabled:
        return
    with ctx.db.session() as session:
        reset_demo(session)
