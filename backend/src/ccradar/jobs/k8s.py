"""Kubernetes jobs."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from ccradar.db.base import utcnow
from ccradar.db.tenant import set_tenant
from ccradar.jobs.context import job_context
from ccradar.jobs.registry import job


@job("k8s.allocate_costs", queue="maintenance", cron="3 * * * *")
def allocate_costs() -> int:
    """Allocate the hour that just ended for every reporting cluster."""
    from ccradar.k8s.allocation import allocate, due_clusters

    now = utcnow()
    hour = (now - timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    ctx = job_context()
    with ctx.db.session() as session:
        set_tenant(session, bypass=True)
        clusters = [(c.id, c.org_id) for c in due_clusters(session, now)]
    for cluster_id, org_id in clusters:
        with ctx.db.session() as session:
            from ccradar.models import K8sCluster

            set_tenant(session, org_id=org_id)
            cluster = session.get(K8sCluster, cluster_id)
            if cluster is not None:
                allocate(session, cluster, hour)
    return len(clusters)


@job("k8s.detect_health", queue="maintenance", cron="*/5 * * * *")
def detect_health() -> int:
    """Open or resolve automatic incidents for every reporting cluster (not the demo)."""
    from ccradar.k8s.allocation import due_clusters
    from ccradar.k8s.health import detect
    from ccradar.models import K8sCluster, Organization

    now = utcnow()
    ctx = job_context()
    with ctx.db.session() as session:
        set_tenant(session, bypass=True)
        demo_orgs = set(
            session.scalars(select(Organization.id).where(Organization.is_demo.is_(True)))
        )
        clusters = [
            (c.id, c.org_id) for c in due_clusters(session, now) if c.org_id not in demo_orgs
        ]
    changed = 0
    for cluster_id, org_id in clusters:
        with ctx.db.session() as session:
            set_tenant(session, org_id=org_id)
            cluster = session.get(K8sCluster, cluster_id)
            if cluster is not None:
                result = detect(session, cluster, now, ctx.settings.base_url)
                changed += result["opened"] + result["resolved"]
    return changed


@job("k8s.check_agents", queue="maintenance", cron="*/10 * * * *")
def check_agents() -> int:
    """Silent / back-again notifications for every organisation's clusters (not the demo)."""
    from ccradar.k8s.health import check_agents as check
    from ccradar.models import Organization

    now = utcnow()
    ctx = job_context()
    with ctx.db.session() as session:
        set_tenant(session, bypass=True)
        orgs = list(session.scalars(select(Organization.id).where(Organization.is_demo.is_(False))))
    changed = 0
    for org_id in orgs:
        with ctx.db.session() as session:
            set_tenant(session, org_id=org_id)
            result = check(session, now, ctx.settings.base_url)
            changed += result["silent"] + result["back"]
    return changed
