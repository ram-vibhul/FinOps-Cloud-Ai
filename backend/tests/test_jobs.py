from __future__ import annotations

import procrastinate
import pytest
from procrastinate.testing import InMemoryConnector

from ccradar.jobs import InProcessRunner, JobRegistry, ProcrastinateRunner, job
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


@pytest.mark.skipif(not POSTGRES_URL, reason="needs CCRADAR_TEST_DATABASE_URL (PostgreSQL)")
def test_procrastinate_worker_runs_deferred_job_on_postgres(
    admin_database_url: str, database_url: str
) -> None:
    """End to end on PostgreSQL: migrate (incl. queue schema), defer, run the worker."""
    import asyncio

    from ccradar.jobs.runner import build_procrastinate_app, run_worker_async
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


def test_every_job_module_registers() -> None:
    """Guards against registration imports being removed by an automatic clean-up."""
    from ccradar.jobs import load_all_jobs, registry

    load_all_jobs()
    expected = {
        "email.send",
        "costs.import_cost_explorer",
        "costs.import_cur",
        "costs.import_file",
        "costs.detect_anomalies",
        "fx.refresh",
        "demo.reset",
        "privacy.apply_retention",
        "system.cleanup_rate_limits",
        "inventory.collect",
        "changes.import_cloudtrail",
    }
    assert expected <= set(registry.jobs)


def test_scheduled_jobs_accept_procrastinate_timestamp() -> None:
    """Procrastinate calls periodic jobs with ``timestamp=``; every one must accept it
    (before this, every scheduled job failed in the PostgreSQL worker)."""
    import inspect
    from typing import Any

    from ccradar.jobs import load_all_jobs, registry
    from ccradar.jobs.runner import ProcrastinateRunner

    load_all_jobs()
    registered: dict[str, Any] = {}

    class FakeApp:
        def task(self, *, name: str, **_: Any):
            def wrap(func: Any) -> Any:
                registered[name] = func
                return func

            return wrap

        def periodic(self, **_: Any):
            return lambda task: task

    ProcrastinateRunner(FakeApp())
    crons = [s for s in registry.jobs.values() if s.cron]
    assert len(crons) > 30
    for spec in crons:
        inspect.signature(registered[spec.name], follow_wrapped=False).bind(timestamp=1794278400)
    calls: list[Any] = []

    def sample(x: int = 1) -> int:
        calls.append(x)
        return x

    from ccradar.jobs.runner import periodic_safe

    assert periodic_safe(sample)(timestamp=5) == 1 and calls == [1]
