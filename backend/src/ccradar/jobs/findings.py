"""Daily savings detectors."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from ccradar.db.tenant import set_tenant
from ccradar.jobs.context import job_context
from ccradar.jobs.registry import job


@job("findings.run_all", queue="maintenance", cron="40 6 * * *")
def run_all() -> int:
    from ccradar.findings.service import run
    from ccradar.models import Organization

    ctx = job_context()
    with ctx.db.session() as session:
        set_tenant(session, bypass=True)
        orgs = list(session.scalars(select(Organization.id).where(Organization.is_demo.is_(False))))
    for org_id in orgs:
        with ctx.db.session() as session:
            set_tenant(session, org_id=org_id)
            run(session, org_id)
    return len(orgs)


@job("commitments.collect_all", queue="imports", cron="50 5 * * *")
def collect_commitments() -> int:
    """Daily Savings Plans / RI snapshot per active AWS connection (before the detectors)."""
    from ccradar.clouds.aws.commitment_job import snapshot
    from ccradar.jobs.costs import gateway
    from ccradar.models import CloudConnection, Organization

    ctx = job_context()
    with ctx.db.session() as session:
        set_tenant(session, bypass=True)
        demo = set(session.scalars(select(Organization.id).where(Organization.is_demo.is_(True))))
        ids = [
            c.id
            for c in session.scalars(
                select(CloudConnection).where(
                    CloudConnection.provider == "aws", CloudConnection.status == "active"
                )
            )
            if c.org_id not in demo
        ]
    from ccradar.clouds.aws.commitment_inventory import collect, expiry_alerts

    done = 0
    for connection_id in ids:
        with ctx.db.session() as session:
            done += snapshot(session, gateway(), connection_id) is not None
        with ctx.db.session() as session:
            collect(session, gateway(), connection_id)
    with ctx.db.session() as session:
        set_tenant(session, bypass=True)
        orgs = list(session.scalars(select(Organization.id).where(Organization.is_demo.is_(False))))
    for org_id in orgs:
        with ctx.db.session() as session:
            set_tenant(session, org_id=org_id)
            expiry_alerts(session)
    return done


@job("advice.collect_all", queue="imports", cron="20 6 * * *")
def collect_advice() -> int:
    """Import the clouds' own recommendations per active connection (before the detectors):
    AWS Cost Optimization Hub / Compute Optimizer / Trusted Advisor, and Azure Advisor."""
    from ccradar.models import CloudConnection, Organization

    ctx = job_context()
    with ctx.db.session() as session:
        set_tenant(session, bypass=True)
        demo = set(session.scalars(select(Organization.id).where(Organization.is_demo.is_(True))))
        conns = [
            (c.id, c.provider)
            for c in session.scalars(
                select(CloudConnection).where(
                    CloudConnection.provider.in_(("aws", "azure", "gcp")),
                    CloudConnection.status == "active",
                )
            )
            if c.org_id not in demo
        ]
    for connection_id, provider in conns:
        advice_for(connection_id, provider)
    return len(conns)


def advice_for(connection_id: uuid.UUID, provider: str) -> None:
    """The cloud's own recommendations for one connection."""
    from ccradar.clouds.aws.advice import collect
    from ccradar.clouds.azure import advisor
    from ccradar.clouds.azure.client import for_connection
    from ccradar.clouds.gcp import client as gcp_client
    from ccradar.clouds.gcp import recommender
    from ccradar.jobs.costs import gateway
    from ccradar.models import CloudConnection

    with job_context().db.session() as session:
        if provider in ("azure", "gcp"):
            set_tenant(session, bypass=True)
            conn = session.get(CloudConnection, connection_id)
            if conn is None:
                return
            if provider == "azure":
                advisor.collect(session, for_connection(conn), connection_id)
            else:
                recommender.collect(session, gcp_client.for_connection(conn), connection_id)
        elif provider == "aws":
            collect(session, gateway(), connection_id)


@job("findings.verify", queue="maintenance", cron="10 7 * * *")
def verify_findings() -> int:
    """Measure applied savings in the bill; verify the ones that show up (after imports)."""
    from ccradar.findings.workflow import verify_applied
    from ccradar.models import Organization

    ctx = job_context()
    with ctx.db.session() as session:
        set_tenant(session, bypass=True)
        orgs = list(session.scalars(select(Organization.id).where(Organization.is_demo.is_(False))))
    verified = 0
    for org_id in orgs:
        with ctx.db.session() as session:
            set_tenant(session, org_id=org_id)
            verified += verify_applied(session)["verified"]
    return verified


@job("purchases.verify", queue="maintenance", cron="40 7 2 * *")
def verify_purchases() -> int:
    """Measure last month's savings for every commitment bought through a plan."""
    from ccradar.costs.purchases import verify
    from ccradar.models import Organization

    ctx = job_context()
    with ctx.db.session() as session:
        set_tenant(session, bypass=True)
        orgs = [
            (o.id, o.default_currency)
            for o in session.scalars(select(Organization).where(Organization.is_demo.is_(False)))
        ]
    done = 0
    for org_id, currency in orgs:
        with ctx.db.session() as session:
            set_tenant(session, org_id=org_id)
            done += verify(session, org_id, currency)
    return done


@job("flow_logs.collect_all", queue="imports", cron="30 4 * * *")
def collect_flow_logs() -> int:
    """Attribute yesterday's network transfer cost from VPC Flow Logs, where turned on."""
    from ccradar.costs.flow_logs import collect
    from ccradar.jobs.costs import gateway
    from ccradar.models import CloudConnection

    ctx = job_context()
    with ctx.db.session() as session:
        set_tenant(session, bypass=True)
        ids = [
            c.id
            for c in session.scalars(
                select(CloudConnection).where(
                    CloudConnection.provider == "aws", CloudConnection.status == "active"
                )
            )
            if ((c.settings or {}).get("flow_logs") or {}).get("enabled")
        ]
    for connection_id in ids:
        with ctx.db.session() as session:
            collect(session, gateway(), connection_id)
    return len(ids)


@job("findings.recheck_kept", queue="maintenance", cron="50 7 3 * *")
def recheck_kept() -> int:
    """Recheck last month's bill for every verified saving (regressions reopen)."""
    from ccradar.findings.kept import recheck
    from ccradar.models import Organization

    ctx = job_context()
    with ctx.db.session() as session:
        set_tenant(session, bypass=True)
        orgs = list(session.scalars(select(Organization.id).where(Organization.is_demo.is_(False))))
    regressed = 0
    for org_id in orgs:
        with ctx.db.session() as session:
            set_tenant(session, org_id=org_id)
            regressed += recheck(session)["regressed"]
    return regressed


@job("fixes.run_approved", queue="default", cron="* * * * *")
def run_approved_fixes() -> int:
    """Every minute: run approved automatic fixes (every guard is checked again inside).

    Picked up from the database rather than enqueued by the approving request, so a fix
    never runs before its approval is committed."""
    from ccradar.clouds.aws.fixes import execute
    from ccradar.jobs.costs import gateway
    from ccradar.models import FixAction

    ctx = job_context()
    with ctx.db.session() as session:
        set_tenant(session, bypass=True)
        ids = list(
            session.scalars(select(FixAction.id).where(FixAction.status == "approved").limit(50))
        )
    for fix_id in ids:
        with ctx.db.session() as session:
            execute(session, gateway(), fix_id)
    return len(ids)


@job("fixes.file_requests", queue="default", cron="30 8 * * *")
def file_fix_requests() -> int:
    """Daily: auto clean-up files fix requests (where an owner turned it on) and tells
    the people who approve them. Nothing runs until someone approves."""
    from ccradar.clouds.aws.fixes import auto_request
    from ccradar.core.notify import Message, notify
    from ccradar.models import CloudConnection

    ctx = job_context()
    with ctx.db.session() as session:
        set_tenant(session, bypass=True)
        conns = [
            (c.id, c.org_id)
            for c in session.scalars(
                select(CloudConnection).where(
                    CloudConnection.provider == "aws", CloudConnection.status == "active"
                )
            )
        ]
    filed = 0
    for conn_id, org_id in conns:
        with ctx.db.session() as session:
            set_tenant(session, org_id=org_id)
            conn = session.get(CloudConnection, conn_id)
            new = auto_request(session, conn) if conn else []
            if new:
                total = sum(x.savings_monthly for x in new)
                notify(
                    session,
                    "fix_request",
                    [
                        Message(
                            title=f"{len(new)} clean-up(s) waiting for approval",
                            text=f"Idle for a week or more; about {total:,.2f} a month in total.",
                            link="/savings",
                            severity="info",
                        )
                    ],
                )
            filed += len(new)
    return filed
