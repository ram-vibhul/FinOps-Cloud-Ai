from __future__ import annotations

import procrastinate
import pytest
from procrastinate.testing import InMemoryConnector

from costlens.jobs import InProcessRunner, JobRegistry, ProcrastinateRunner, job
from tests.conftest import POSTGRES_URL


def make_registry() -> tuple[JobRegistry, list[int]]:
    reg = JobRegistry()
    calls: list[int] = []

    @job("add", into=reg)
    def add(x: int) -> None:
        calls.append(x)

    @job("async_add", into=reg)
    async def async_add(x: int) -> None:
        calls.append(x * 10)

    @job("boom", into=reg, cron="0 * * * *")
    def boom() -> None:
        raise RuntimeError("bad")

    return reg, calls


def test_duplicate_job_rejected() -> None:
    reg, _ = make_registry()
    with pytest.raises(ValueError, match="already registered"):
        job("add", into=reg)(lambda: None)


def test_in_process_runner_runs_sync_and_async() -> None:
    reg, calls = make_registry()
    runner = InProcessRunner(reg)
    runner.enqueue("add", x=1)
    runner.enqueue("async_add", x=2)
    assert calls == [1, 20]
    assert runner.completed == ["add", "async_add"]


def test_in_process_runner_records_failures_without_raising() -> None:
    reg, _ = make_registry()
    runner = InProcessRunner(reg)
    runner.enqueue("boom")
    assert runner.failed and runner.failed[0][0] == "boom"


def test_unknown_job() -> None:
    reg, _ = make_registry()
    with pytest.raises(KeyError):
        InProcessRunner(reg).enqueue("nope")


def test_procrastinate_runner_defers_jobs() -> None:
    reg, calls = make_registry()
    connector = InMemoryConnector()
    app = procrastinate.App(connector=connector)
    runner = ProcrastinateRunner(app, reg)
    with app.open():
        runner.enqueue("add", x=5)
        jobs = list(connector.jobs.values())
        assert len(jobs) == 1
        assert jobs[0]["task_name"] == "add"
        assert jobs[0]["args"] == {"x": 5}
    assert calls == []  # deferred, not run inline


@pytest.mark.skipif(not POSTGRES_URL, reason="needs COSTLENS_TEST_DATABASE_URL (PostgreSQL)")
def test_procrastinate_worker_runs_deferred_job_on_postgres(
    admin_database_url: str, database_url: str
) -> None:
    """End to end on PostgreSQL: migrate (incl. queue schema), defer, run the worker."""
    import asyncio

    from costlens.jobs.runner import build_procrastinate_app, run_worker_async
    from tests.conftest import migrate_for_tests

    migrate_for_tests(admin_database_url)  # worker below runs as the app role
    reg = JobRegistry()
    calls: list[int] = []

    @job("pg_add", into=reg)
    def pg_add(x: int) -> None:
        calls.append(x)

    app = build_procrastinate_app(database_url)
    runner = ProcrastinateRunner(app, reg)
    with app.open():
        runner.enqueue("pg_add", x=7)
    assert calls == []
    asyncio.run(run_worker_async(app, wait=False))
    assert calls == [7]
