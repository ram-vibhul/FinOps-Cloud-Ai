"""Kubernetes clusters connected through the in-cluster agent, and what it reports."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from costlens.db.base import Base, Timestamps, UUIDPrimaryKey
from costlens.db.tenant import TenantMixin


class K8sCluster(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "k8s_clusters"
    __audited__ = True
    __audit_redact__ = frozenset({"token_hash", "token_hint"})
    __audit_ignore__ = frozenset(
        {"status", "agent_version", "kubernetes_version", "provider", "node_count", "last_seen_at"}
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
