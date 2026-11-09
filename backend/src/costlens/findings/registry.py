"""Detectors register here. Each returns the findings it sees now for one organisation;
the service stores them, keeps history and marks what disappeared."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy.orm import Session

Level = Literal["low", "medium", "high"]


@dataclass
class FindingIn:
    key: str  # stable identity within the detector (e.g. the volume id)
    title: str
    savings_monthly: float  # in the organisation's display currency
    currency: str
    risk: Level = "low"
    effort: Level = "low"
    description: str = ""
    overlap: str | None = None  # resource the saving comes from; defaults to resource_id/key
    provider: str | None = None
    account_id: str | None = None
    region: str | None = None
    service: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    steps: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)


Detect = Callable[[Session, uuid.UUID, str], list[FindingIn]]


@dataclass
class Detector:
    name: str
    category: str
    title: str
    func: Detect


DETECTORS: dict[str, Detector] = {}
# Modules that register detectors (by name, so lint clean-ups cannot drop them).
DETECTOR_MODULES = (
    "costlens.findings.kubernetes",
    "costlens.findings.aws",
    "costlens.findings.aws_usage",
    "costlens.findings.aws_db",
    "costlens.findings.commitments",
    "costlens.findings.network",
    "costlens.findings.aws_compute",
    "costlens.findings.aws_storage",
    "costlens.findings.aws_databases",
    "costlens.findings.aws_analytics",
    "costlens.findings.aws_platform",
    "costlens.findings.aws_schedule",
    "costlens.findings.aws_db_perf",
    "costlens.findings.upgrades",
    "costlens.findings.ai",
    "costlens.findings.advice",  # last: merges with everything above
)


def detector(name: str, *, category: str, title: str) -> Callable[[Detect], Detect]:
    def wrap(func: Detect) -> Detect:
        DETECTORS[name] = Detector(name, category, title, func)
        return func

    return wrap


def load_all() -> dict[str, Detector]:
    import importlib

    for module in DETECTOR_MODULES:
        importlib.import_module(module)
    return DETECTORS
