"""Job runners: in-process for SQLite, Procrastinate for PostgreSQL."""

from __future__ import annotations

import asyncio
import functools
import inspect
import logging
from typing import Any, Protocol

from costlens.config import Settings
from costlens.core.metrics import JOBS
from costlens.jobs.registry import JobRegistry, registry

log = logging.getLogger(__name__)


class JobRunner(Protocol):
    def enqueue(self, name: str, **kwargs: Any) -> None: ...


class InProcessRunner:
    """Runs jobs immediately in the calling process.

    Used with SQLite (local development, trials) and in tests. Failures are
    logged and recorded, never raised into the request that enqueued them.
    """

    def __init__(self, jobs: JobRegistry | None = None) -> None:
        self.jobs = jobs or registry
        self.completed: list[str] = []
        self.failed: list[tuple[str, str]] = []

    def enqueue(self, name: str, **kwargs: Any) -> None:
        spec = self.jobs.get(name)
        try:
            result = spec.func(**kwargs)
            if inspect.isawaitable(result):
                asyncio.run(_await(result))
        except Exception as exc:
            log.exception("in-process job %s failed", name)
            self.failed.append((name, repr(exc)))
            JOBS.inc(job=name, outcome="failed")
            return
        self.completed.append(name)
        JOBS.inc(job=name, outcome="succeeded")


async def _await(awaitable: Any) -> Any:
    return await awaitable


def periodic_safe(func: Any) -> Any:
    """Procrastinate calls periodic jobs with a ``timestamp`` keyword (the scheduled
    time). Our job functions do not take it; without this wrapper every scheduled job
    failed with a TypeError in the PostgreSQL worker."""
    if inspect.iscoroutinefunction(func):

        @functools.wraps(func)
        async def run_async(*args: Any, timestamp: Any = None, **kwargs: Any) -> Any:
            del timestamp
            return await func(*args, **kwargs)

        return run_async

    @functools.wraps(func)
    def run(*args: Any, timestamp: Any = None, **kwargs: Any) -> Any:
        del timestamp
        return func(*args, **kwargs)

    return run


class ProcrastinateRunner:
    """Defers jobs to the PostgreSQL-backed Procrastinate queue."""

    def __init__(self, app: Any, jobs: JobRegistry | None = None) -> None:
        self.app = app
        self.jobs = jobs or registry
        self._tasks: dict[str, Any] = {}
        for spec in self.jobs.jobs.values():
            func = periodic_safe(spec.func) if spec.cron else spec.func
            task = app.task(name=spec.name, queue=spec.queue, retry=spec.retry)(func)
            if spec.cron:
                app.periodic(cron=spec.cron, periodic_id=spec.name)(task)
            self._tasks[spec.name] = task

    def enqueue(self, name: str, **kwargs: Any) -> None:
        self.jobs.get(name)
        self._tasks[name].defer(**kwargs)


def build_procrastinate_app(conninfo: str) -> Any:
    """Procrastinate app on the async psycopg connector.

    The worker needs the async connector; synchronous calls (deferring a job
    from a web request, applying the schema) transparently use Procrastinate's
    internal synchronous connector when the app is opened with ``app.open()``.
    """
    import procrastinate

    conninfo = conninfo.replace("postgresql+psycopg://", "postgresql://")
    return procrastinate.App(connector=procrastinate.PsycopgConnector(conninfo=conninfo))


async def run_worker_async(app: Any, *, wait: bool = True) -> None:
    async with app.open_async():
        await app.run_worker_async(wait=wait, install_signal_handlers=wait)


def build_runner(settings: Settings) -> JobRunner:
    from costlens.jobs import load_all_jobs

    load_all_jobs()
    if settings.run_jobs_in_process:
        return InProcessRunner()
    app = build_procrastinate_app(settings.database_url)
    app.open()
    return ProcrastinateRunner(app)
