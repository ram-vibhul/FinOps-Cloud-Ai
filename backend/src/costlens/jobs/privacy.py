"""Privacy jobs: build organisation exports, purge deleted organisations, expire exports."""

from __future__ import annotations

import uuid

from costlens.jobs.context import job_context
from costlens.jobs.registry import job
from costlens.privacy import service


@job("privacy.build_export", queue="default", retry=2)
def build_export(export_id: str) -> None:
    ctx = job_context()
    with ctx.db.session() as session:
        try:
            service.build_org_export(session, ctx.storage, uuid.UUID(export_id))
        except Exception as exc:
            session.rollback()
            from costlens.db.tenant import set_tenant
            from costlens.models import DataExport

            set_tenant(session, bypass=True)
            export = session.get(DataExport, uuid.UUID(export_id))
            if export is not None:
                export.status = "failed"
                export.error = type(exc).__name__
            raise


@job("privacy.purge_deleted_organizations", queue="maintenance", cron="40 2 * * *")
def purge_deleted_organizations() -> int:
    ctx = job_context()
    with ctx.db.session() as session:
        return len(service.purge_deleted_orgs(session, ctx.storage))


@job("privacy.cleanup_exports", queue="maintenance", cron="50 2 * * *")
def cleanup_exports() -> int:
    ctx = job_context()
    with ctx.db.session() as session:
        return service.cleanup_expired_exports(session, ctx.storage)


@job("privacy.apply_retention", queue="maintenance", cron="10 3 * * *")
def apply_retention_job() -> dict[str, int]:
    from costlens.privacy.retention import apply_retention

    ctx = job_context()
    with ctx.db.session() as session:
        return apply_retention(session)
