"""A small job registry independent of the queue backend.

Jobs are plain functions registered by name. The runner decides whether they
run in-process (SQLite / tests) or through Procrastinate (PostgreSQL).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

JobFunc = Callable[..., Any]


@dataclass(frozen=True)
class JobSpec:
    name: str
    func: JobFunc
    queue: str = "default"
    cron: str | None = None  # periodic schedule, five-field cron syntax


@dataclass
class JobRegistry:
    jobs: dict[str, JobSpec] = field(default_factory=dict)

    def register(self, spec: JobSpec) -> None:
        if spec.name in self.jobs:
            raise ValueError(f"job {spec.name!r} already registered")
        self.jobs[spec.name] = spec

    def get(self, name: str) -> JobSpec:
        try:
            return self.jobs[name]
        except KeyError:
            raise KeyError(f"unknown job {name!r}") from None


registry = JobRegistry()


def job(
    name: str, *, queue: str = "default", cron: str | None = None, into: JobRegistry | None = None
) -> Callable[[JobFunc], JobFunc]:
    def decorator(func: JobFunc) -> JobFunc:
        (into or registry).register(JobSpec(name=name, func=func, queue=queue, cron=cron))
        return func

    return decorator
