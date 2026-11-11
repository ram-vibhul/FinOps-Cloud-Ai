"""Weekly AWS audit (after the daily detectors have run)."""

from __future__ import annotations

from sqlalchemy import select

from ccradar.db.tenant import set_tenant
from ccradar.jobs.context import job_context
from ccradar.jobs.registry import job


@job("audits.run_all", queue="maintenance", cron="30 7 * * 1")
def run_all() -> int:
    from ccradar.aws_audit.service import run_audit
    from ccradar.models import CloudConnection, Organization

    ctx = job_context()
    with ctx.db.session() as session:
        set_tenant(session, bypass=True)
        orgs = list(
            session.scalars(
                select(Organization.id)
                .where(Organization.is_demo.is_(False))
                .where(
                    Organization.id.in_(
                        select(CloudConnection.org_id).where(CloudConnection.provider == "aws")
                    )
                )
            )
        )
    for org_id in orgs:
        with ctx.db.session() as session:
            set_tenant(session, org_id=org_id)
            run_audit(session, org_id, trigger="scheduled", run_detectors=False)
    return len(orgs)
