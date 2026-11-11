"""Reliability targets: what a service must keep (headroom, utilisation ceiling,
replicas, availability). Savings recommendations respect them."""

from __future__ import annotations

from sqlalchemy import JSON, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ccradar.db.base import Base, Timestamps, UUIDPrimaryKey
from ccradar.db.tenant import TenantMixin


class ReliabilityTarget(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "reliability_targets"
    __audited__ = True

    name: Mapped[str] = mapped_column(String(100))
    # kind: k8s_workload (cluster_id, namespace, workload) | k8s_namespace (namespace)
    # | aws_resource (resource_id) | aws_tag (key, value)
    match: Mapped[dict] = mapped_column(JSON, default=dict)
    tier: Mapped[str] = mapped_column(
        String(20), default="standard"
    )  # critical | standard | best_effort
    availability_pct: Mapped[float] = mapped_column(Float, default=99.9)
    headroom_pct: Mapped[float] = mapped_column(Float, default=20.0)  # above the observed peak
    max_utilization_pct: Mapped[float] = mapped_column(Float, default=80.0)  # after a resize
    min_replicas: Mapped[int] = mapped_column(Integer, default=1)
    notes: Mapped[str] = mapped_column(String(1000), default="")
