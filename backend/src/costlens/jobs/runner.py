"""Job runners: in-process for SQLite, Procrastinate for PostgreSQL."""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any, Protocol

from costlens.config import Settings
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
            return
        self.completed.append(name)


async def _await(awaitable: Any) -> Any:
    return await awaitable


class ProcrastinateRunner:
    """Defers jobs to the PostgreSQL-backed Procrastinate queue."""

    def __init__(self, app: Any, jobs: JobRegistry | None = None) -> None:
        self.app = app
        self.jobs = jobs or registry
        self._tasks: dict[str, Any] = {}
        for spec in self.jobs.jobs.values():
            task = app.task(name=spec.name, queue=spec.queue)(spec.func)
            if spec.cron:
                app.periodic(cron=spec.cron, periodic_id=spec.name)(task)
            self._tasks[spec.name] = task

    def enqueue(self, name: str, **kwargs: Any) -> None:
        self.jobs.get(name)
        self._tasks[name].defer(**kwargs)


def build_procrastinate_app(conninfo: str) -> Any:
    import procrastinate

    conninfo = conninfo.replace("postgresql+psycopg://", "postgresql://")
    return procrastinate.App(connector=procrastinate.SyncPsycopgConnector(conninfo=conninfo))


def build_runner(settings: Settings) -> JobRunner:
    if settings.run_jobs_in_process:
        return InProcessRunner()
    app = build_procrastinate_app(settings.database_url)
    app.open()
    return ProcrastinateRunner(app)
