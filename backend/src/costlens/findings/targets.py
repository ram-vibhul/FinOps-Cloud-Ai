"""Find the reliability target that applies to a workload or resource (most specific wins)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from costlens.findings.registry import FindingIn
from costlens.models import CloudResource, ReliabilityTarget

DEFAULT_HEADROOM = 20.0
DEFAULT_MAX_UTIL = 80.0
HA_AVAILABILITY = 99.95  # at or above: keep Multi-AZ / standby copies
RISK_UP = {"low": "medium", "medium": "high", "high": "high"}


@dataclass
class Effective:
    name: str | None
    tier: str
    availability_pct: float
    headroom_pct: float
    max_utilization_pct: float
    min_replicas: int

    @property
    def critical(self) -> bool:
        return self.tier == "critical"

    def evidence(self) -> dict[str, Any]:
        return (
            {
                "target": self.name,
                "tier": self.tier,
                "headroom_pct": self.headroom_pct,
                "max_utilization_pct": self.max_utilization_pct,
                "min_replicas": self.min_replicas,
                "availability_pct": self.availability_pct,
            }
            if self.name
            else {}
        )


DEFAULT = Effective(None, "standard", 99.9, DEFAULT_HEADROOM, DEFAULT_MAX_UTIL, 1)


class Targets:
    """All targets of the current organisation, loaded once per detector run."""

    def __init__(self, db: Session) -> None:
        self.rows = list(db.scalars(select(ReliabilityTarget)))

    @staticmethod
    def _effective(t: ReliabilityTarget) -> Effective:
        # Never weaker than the defaults: targets only add safety.
        return Effective(
            t.name,
            t.tier,
            t.availability_pct,
            max(t.headroom_pct, DEFAULT_HEADROOM),
            min(t.max_utilization_pct, DEFAULT_MAX_UTIL),
            max(t.min_replicas, 1),
        )

    def _first(self, *conditions: Any) -> Effective | None:
        for cond in conditions:
            for t in self.rows:
                if cond(t.match or {}):
                    return self._effective(t)
        return None

    def for_workload(self, cluster_id: str, namespace: str, workload: str) -> Effective:
        return (
            self._first(
                lambda m: (
                    m.get("kind") == "k8s_workload"
                    and m.get("cluster_id") == cluster_id
                    and m.get("namespace") == namespace
                    and m.get("workload") == workload
                ),
                lambda m: (
                    m.get("kind") == "k8s_namespace"
                    and m.get("cluster_id") in (cluster_id, None, "")
                    and m.get("namespace") == namespace
                ),
            )
            or DEFAULT
        )

    def for_resource(self, resource_id: str, tags: dict[str, str] | None) -> Effective:
        tags = {k.lower(): str(v).lower() for k, v in (tags or {}).items()}
        return (
            self._first(
                lambda m: m.get("kind") == "aws_resource" and m.get("resource_id") == resource_id,
                lambda m: (
                    m.get("kind") == "aws_tag"
                    and tags.get(str(m.get("key", "")).lower()) == str(m.get("value", "")).lower()
                ),
            )
            or DEFAULT
        )


DESTRUCTIVE = ("stop-instances", "terminate-instances", "delete-", "release-address")


def _destructive(item: FindingIn) -> bool:
    return item.key.startswith("idle:") or any(
        word in cmd for cmd in item.commands for word in DESTRUCTIVE
    )


def _too_hot(item: FindingIn, max_util: float) -> bool:
    """A size down halves capacity: the observed peaks must still fit under the ceiling."""
    ev = item.evidence
    peaks = [ev.get("cpu_max"), ev.get("mem_max"), ev.get("memory_used_max_pct")]
    return any(p is not None and float(p) * 2 > max_util for p in peaks)


def apply(db: Session, detector_name: str, items: list[FindingIn]) -> list[FindingIn]:
    """Drop or adjust findings that would break a reliability target."""
    targets = Targets(db)
    if not targets.rows:
        return items
    ids = [i.resource_id for i in items if i.provider == "aws" and i.resource_id]
    tags = (
        dict(
            db.execute(
                select(CloudResource.resource_id, CloudResource.tags).where(
                    CloudResource.resource_id.in_(ids)
                )
            ).all()
        )
        if ids
        else {}
    )
    out = []
    for item in items:
        if item.provider == "kubernetes" and item.resource_id:
            parts = item.resource_id.split("/", 3)
            eff = targets.for_workload(parts[0], parts[1], parts[3]) if len(parts) == 4 else DEFAULT
        elif item.resource_id:
            eff = targets.for_resource(item.resource_id, tags.get(item.resource_id))
        else:
            eff = DEFAULT
        if eff.name is None:
            out.append(item)
            continue
        if eff.critical and _destructive(item):
            continue  # never suggest stopping or deleting a critical service
        if detector_name == "aws.rds_multi_az_nonprod" and eff.availability_pct >= HA_AVAILABILITY:
            continue  # the availability target needs the standby
        if item.key.startswith("size:") and _too_hot(item, eff.max_utilization_pct):
            continue
        if eff.critical:
            item.risk = RISK_UP[item.risk]  # type: ignore[assignment]
        item.evidence = {**item.evidence, "reliability": eff.evidence()}
        item.description = (
            f"{item.description} Respects reliability target “{eff.name}” "
            f"({eff.tier.replace('_', ' ')}, {eff.availability_pct:g}% availability)."
        ).strip()
        out.append(item)
    return out
