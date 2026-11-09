"""Retention for Kubernetes data reported by the agent."""

from __future__ import annotations

from costlens.privacy.retention import RetentionPolicy, register

register(
    RetentionPolicy(
        kind="k8s_events",
        label="Kubernetes events",
        description="Warnings and other events reported by the cluster agent.",
        table="k8s_events",
        time_column="last_at",
        default_days=30,
        min_days=7,
        max_days=400,
    )
)
register(
    RetentionPolicy(
        kind="k8s_removed_objects",
        label="Removed Kubernetes objects",
        description="Pods, workloads and nodes that no longer exist (live ones are always kept).",
        table="k8s_objects",
        time_column="deleted_at",
        default_days=30,
        min_days=7,
        max_days=400,
    )
)
register(
    RetentionPolicy(
        kind="k8s_usage",
        label="Kubernetes usage history",
        description="Hourly CPU and memory use per workload, used for right-sizing.",
        table="k8s_usage",
        time_column="hour",
        default_days=90,
        min_days=14,
        max_days=400,
    )
)
register(
    RetentionPolicy(
        kind="k8s_metric_points",
        label="Custom metric history",
        description="Values of your own Prometheus queries.",
        table="k8s_metric_points",
        time_column="at",
        default_days=30,
        min_days=7,
        max_days=400,
    )
)
