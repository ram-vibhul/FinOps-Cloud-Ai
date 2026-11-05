from costlens.jobs.registry import JobRegistry, job, registry
from costlens.jobs.runner import InProcessRunner, JobRunner, ProcrastinateRunner, build_runner

__all__ = [
    "InProcessRunner",
    "JobRegistry",
    "JobRunner",
    "ProcrastinateRunner",
    "build_runner",
    "job",
    "registry",
]


JOB_MODULES = ("costs", "demo", "email", "fx", "privacy", "system")


def load_all_jobs() -> None:
    """Import every module that registers jobs (by name, so lint fixes cannot drop one)."""
    import importlib

    for name in JOB_MODULES:
        importlib.import_module(f"costlens.jobs.{name}")
