"""What background jobs need (database, settings, storage), set once per process.

The web process sets it at startup (so in-process jobs on SQLite and in tests use
the same database as the app); the worker sets it from settings.
"""

from __future__ import annotations

from dataclasses import dataclass

from ccradar.config import Settings
from ccradar.core.storage import Storage
from ccradar.db.session import Database


@dataclass(frozen=True)
class JobContext:
    settings: Settings
    db: Database
    storage: Storage


_ctx: JobContext | None = None


def configure_jobs(ctx: JobContext) -> None:
    global _ctx  # noqa: PLW0603 - process-wide, set at startup
    _ctx = ctx


def job_context() -> JobContext:
    if _ctx is None:
        raise RuntimeError("job context is not configured")
    return _ctx
