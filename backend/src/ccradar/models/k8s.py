"""Kubernetes clusters connected through the in-cluster agent, and what it reports."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from ccradar.db.base import Base, Timestamps, UUIDPrimaryKey
from ccradar.db.tenant import TenantMixin


class K8sCluster(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "k8s_clusters"
    __audited__ = True
    __audit_redact__ = frozenset({"token_hash", "token_hint"})
    __audit_ignore__ = frozenset(
        {
            "status",
            "agent_version",
            "kubernetes_version",
            "provider",
            "node_count",
            "last_seen_at",
            "silent_since",
        }
    )
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_k8s_clusters_name"),)

    name: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(64))  # sha256 of the secret part
    token_hint: Mapped[str] = mapped_column(String(12))  # last characters, to recognise it
    status: Mapped[str] = mapped_column(String(20), default="waiting")  # waiting | connected
    agent_version: Mapped[str | None] = mapped_column(String(40))
    kubernetes_version: Mapped[str | None] = mapped_column(String(40))
    provider: Mapped[str | None] = mapped_column(String(40))  # eks | gke | aks | other
    node_count: Mapped[int | None] = mapped_column(Integer)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    silent_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # warned once
    settings: Mapped[dict] = mapped_column(JSON, default=dict)


class K8sObject(TenantMixin, UUIDPrimaryKey, Base):
    """Latest trimmed state of one object (pod, node, workload, namespace...)."""

    __tablename__ = "k8s_objects"
    __table_args__ = (UniqueConstraint("cluster_id", "kind", "uid", name="uq_k8s_objects_uid"),)

    cluster_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("k8s_clusters.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(40))
    namespace: Mapped[str | None] = mapped_column(String(253))
    name: Mapped[str] = mapped_column(String(253))
    uid: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    snapshot_id: Mapped[str] = mapped_column(String(64))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class K8sEvent(TenantMixin, UUIDPrimaryKey, Base):
    __tablename__ = "k8s_events"
    __table_args__ = (UniqueConstraint("cluster_id", "uid", name="uq_k8s_events_uid"),)

    cluster_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("k8s_clusters.id", ondelete="CASCADE"), index=True
    )
    uid: Mapped[str] = mapped_column(String(64))
    namespace: Mapped[str | None] = mapped_column(String(253))
    object_kind: Mapped[str | None] = mapped_column(String(40))
    object_name: Mapped[str | None] = mapped_column(String(253))
    type: Mapped[str] = mapped_column(String(20))  # Normal | Warning
    reason: Mapped[str] = mapped_column(String(128))
    message: Mapped[str] = mapped_column(String(2000))
    count: Mapped[int] = mapped_column(Integer, default=1)
    first_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class K8sCost(TenantMixin, UUIDPrimaryKey, Base):
    """Allocated cost for one hour: per workload (by requests), plus idle per node."""

    __tablename__ = "k8s_costs"
    __table_args__ = (Index("ix_k8s_costs_cluster_hour", "cluster_id", "hour"),)

    cluster_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("k8s_clusters.id", ondelete="CASCADE")
    )
    hour: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    namespace: Mapped[str | None] = mapped_column(String(253))  # None for idle
    kind: Mapped[str] = mapped_column(String(40))  # Deployment | StatefulSet | ... | Idle
    name: Mapped[str] = mapped_column(String(253))  # workload name, or node name for idle
    node: Mapped[str | None] = mapped_column(String(253))
    cpu_request: Mapped[float] = mapped_column(Float, default=0.0)  # cores
    memory_request: Mapped[float] = mapped_column(Float, default=0.0)  # GiB
    cost: Mapped[float] = mapped_column(Numeric(18, 6))
    currency: Mapped[str] = mapped_column(String(3))
    source: Mapped[str] = mapped_column(String(10))  # billing | estimate


class K8sUsage(TenantMixin, UUIDPrimaryKey, Base):
    """Hourly usage of one container of a workload (across its replicas), with the
    requests and limits in force. Peaks are per replica, so they compare to requests."""

    __tablename__ = "k8s_usage"
    __table_args__ = (
        UniqueConstraint(
            "cluster_id", "hour", "namespace", "kind", "name", "container", name="uq_k8s_usage_key"
        ),
    )

    cluster_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("k8s_clusters.id", ondelete="CASCADE"), index=True
    )
    hour: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    namespace: Mapped[str] = mapped_column(String(253))
    kind: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(253))
    container: Mapped[str] = mapped_column(String(253))
    samples: Mapped[int] = mapped_column(Integer, default=0)  # pod-samples this hour
    cpu_sum: Mapped[float] = mapped_column(Float, default=0.0)  # cores, summed over samples
    cpu_max: Mapped[float] = mapped_column(Float, default=0.0)  # one replica's peak
    memory_sum: Mapped[float] = mapped_column(Float, default=0.0)  # GiB
    memory_max: Mapped[float] = mapped_column(Float, default=0.0)
    cpu_request: Mapped[float] = mapped_column(Float, default=0.0)  # per replica
    memory_request: Mapped[float] = mapped_column(Float, default=0.0)
    cpu_limit: Mapped[float | None] = mapped_column(Float)
    memory_limit: Mapped[float | None] = mapped_column(Float)
    replicas: Mapped[int] = mapped_column(Integer, default=0)  # most pods seen in one sample


class K8sInvestigation(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    """A saved investigation: target, window and the ranked causes found."""

    __tablename__ = "k8s_investigations"
    __audited__ = True
    __audit_ignore__ = frozenset({"result"})

    cluster_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("k8s_clusters.id", ondelete="CASCADE"), index=True
    )
    namespace: Mapped[str | None] = mapped_column(String(253))
    kind: Mapped[str | None] = mapped_column(String(40))
    name: Mapped[str | None] = mapped_column(String(253))
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    trigger: Mapped[str] = mapped_column(String(20), default="manual")  # manual | alert | auto
    fingerprint: Mapped[str | None] = mapped_column(String(300), index=True)  # alert de-dup
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | closed
    title: Mapped[str] = mapped_column(String(300))
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    # Closing with the real cause (feeds diagnosis accuracy).
    real_cause: Mapped[str | None] = mapped_column(String(40))  # a check id, or "other"
    cause_note: Mapped[str | None] = mapped_column(String(1000))
    fix_note: Mapped[str | None] = mapped_column(String(1000))
    top_cause: Mapped[str | None] = mapped_column(String(40))  # what CloudCostRadar ranked first
    cause_rank: Mapped[int | None] = mapped_column(
        Integer
    )  # rank of the real cause (None = missing)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class K8sAgentTask(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    """A read-only task for the cluster agent (fetch logs, describe). Allow-listed."""

    __tablename__ = "k8s_agent_tasks"
    __audited__ = True
    __audit_ignore__ = frozenset({"result", "status", "claimed_at", "finished_at", "error"})

    cluster_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("k8s_clusters.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(20))  # logs | describe
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    # queued | running | done | failed | expired
    result: Mapped[str | None] = mapped_column(String(300_000))
    error: Mapped[str | None] = mapped_column(String(1000))
    requested_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class K8sMetricQuery(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    """A customer's own PromQL query, run by the agent every few minutes."""

    __tablename__ = "k8s_metric_queries"
    __audited__ = True
    __audit_ignore__ = frozenset({"last_run_at", "last_error"})

    cluster_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("k8s_clusters.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    promql: Mapped[str] = mapped_column(String(500))
    unit: Mapped[str] = mapped_column(String(20), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))


class K8sMetricPoint(TenantMixin, UUIDPrimaryKey, Base):
    """One value of one series of a customer query at one time."""

    __tablename__ = "k8s_metric_points"
    __table_args__ = (Index("ix_k8s_metric_points_query_at", "query_id", "at"),)

    cluster_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("k8s_clusters.id", ondelete="CASCADE")
    )
    query_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("k8s_metric_queries.id", ondelete="CASCADE")
    )
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    series: Mapped[str] = mapped_column(String(300))  # stable key from the labels
    labels: Mapped[dict] = mapped_column(JSON, default=dict)
    value: Mapped[float] = mapped_column(Float)
