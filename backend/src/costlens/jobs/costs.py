"""Cost import jobs."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from costlens.clouds.aws import cost_explorer, cur
from costlens.clouds.aws.client import AwsGateway, Boto3Gateway
from costlens.db.tenant import set_tenant
from costlens.jobs.context import job_context
from costlens.jobs.registry import job
from costlens.models import CloudConnection

_gateway: AwsGateway | None = None


def gateway() -> AwsGateway:
    """The AWS gateway for jobs (the web app's, or a real one in the worker)."""
    global _gateway  # noqa: PLW0603
    if _gateway is None:
        _gateway = Boto3Gateway(region=job_context().settings.aws_region)
    return _gateway


def use_gateway(value: AwsGateway) -> None:
    global _gateway  # noqa: PLW0603
    _gateway = value


def _after_import(org_id: object) -> None:
    """New cost data: look for anomalies right away (and alert)."""
    from costlens.jobs.runner import build_runner

    build_runner(job_context().settings).enqueue("costs.detect_anomalies", org_id=str(org_id))


@job("costs.import_cost_explorer", queue="imports", retry=3)
def import_cost_explorer(connection_id: str, months: int = 2) -> int:
    ctx = job_context()
    with ctx.db.session() as session:
        runs = cost_explorer.import_connection(
            session, gateway(), uuid.UUID(connection_id), months=months
        )
        rows, org_id = sum(r.rows for r in runs), (runs[0].org_id if runs else None)
    if org_id:
        _after_import(org_id)
    return rows


@job("costs.import_cur", queue="imports", retry=3)
def import_cur(connection_id: str, months: int = 3, force: bool = False) -> int:
    ctx = job_context()
    with ctx.db.session() as session:
        runs = cur.import_connection(
            session, gateway(), uuid.UUID(connection_id), months=months, force=force
        )
        rows, org_id = sum(r.rows for r in runs), (runs[0].org_id if runs else None)
    if org_id:
        _after_import(org_id)
    return rows


@job("costs.schedule_imports", queue="maintenance", cron="15 */6 * * *")
def schedule_imports() -> int:
    """Queue imports for every active AWS connection: CUR when configured, else Cost Explorer."""
    from costlens.jobs.runner import build_runner

    ctx = job_context()
    with ctx.db.session() as session:
        set_tenant(session, bypass=True)
        rows = session.execute(
            select(CloudConnection.id, CloudConnection.cur_bucket).where(
                CloudConnection.provider == "aws", CloudConnection.status == "active"
            )
        ).all()
    runner = build_runner(ctx.settings)
    for connection_id, bucket in rows:
        name = "costs.import_cur" if bucket else "costs.import_cost_explorer"
        runner.enqueue(name, connection_id=str(connection_id))
    return len(rows)


@job("costs.import_file", queue="imports", retry=0)
def import_file(import_id: str) -> int:
    from costlens.costs.files import run_import

    ctx = job_context()
    with ctx.db.session() as session:
        record = run_import(session, ctx.storage, uuid.UUID(import_id))
        org_id, imported = record.org_id, record.status == "imported"
        rows = record.rows_imported
    if imported:
        _after_import(org_id)
    return rows


@job("costs.detect_anomalies", queue="default", retry=1)
def detect_anomalies(org_id: str) -> int:
    """Detect anomalies for one organisation and email owners/admins about new ones."""
    from costlens.core.email import email_sender
    from costlens.costs.alerts import pending_alert
    from costlens.costs.anomalies import detect

    ctx = job_context()
    with ctx.db.session() as session:
        created = detect(session, uuid.UUID(org_id))
        emails = pending_alert(session, uuid.UUID(org_id), ctx.settings.base_url)
    for message in emails:
        email_sender().send(message)
    return len(created)


@job("costs.detect_all_anomalies", queue="maintenance", cron="30 7 * * *")
def detect_all_anomalies() -> int:
    from costlens.jobs.runner import build_runner
    from costlens.models import CostDaily

    ctx = job_context()
    with ctx.db.session() as session:
        set_tenant(session, bypass=True)
        org_ids = session.scalars(select(CostDaily.org_id).distinct()).all()
    runner = build_runner(ctx.settings)
    for org_id in org_ids:
        runner.enqueue("costs.detect_anomalies", org_id=str(org_id))
    return len(org_ids)


@job("changes.import_cloudtrail", queue="imports", retry=1)
def import_cloudtrail(connection_id: str) -> int:
    from costlens.changes.cloudtrail import import_connection

    ctx = job_context()
    with ctx.db.session() as session:
        return import_connection(session, gateway(), uuid.UUID(connection_id))


@job("changes.schedule_cloudtrail", queue="maintenance", cron="5 * * * *")
def schedule_cloudtrail() -> int:
    from costlens.jobs.runner import build_runner

    ctx = job_context()
    with ctx.db.session() as session:
        set_tenant(session, bypass=True)
        ids = session.scalars(
            select(CloudConnection.id).where(
                CloudConnection.provider == "aws", CloudConnection.status == "active"
            )
        ).all()
    runner = build_runner(ctx.settings)
    for connection_id in ids:
        runner.enqueue("changes.import_cloudtrail", connection_id=str(connection_id))
    return len(ids)


@job("costs.reapply_tag_rules", queue="default", retry=1)
def reapply_tag_rules(org_id: str) -> int:
    from costlens.costs.tagrules import reapply
    from costlens.db.tenant import set_tenant as scope

    ctx = job_context()
    with ctx.db.session() as session:
        scope(session, org_id=uuid.UUID(org_id))
        return reapply(session, uuid.UUID(org_id))


@job("inventory.collect", queue="imports", retry=1)
def collect_inventory(connection_id: str) -> int:
    from costlens.inventory.service import collect_aws

    ctx = job_context()
    with ctx.db.session() as session:
        counts = collect_aws(session, gateway(), uuid.UUID(connection_id))
        set_tenant(session, bypass=True)
        conn = session.get(CloudConnection, uuid.UUID(connection_id))
        if (
            conn is not None
            and not conn.cur_bucket
            and (conn.settings or {}).get("ce_resource_level")
        ):
            from costlens.db.base import utcnow
            from costlens.inventory import ce_fallback

            aws_session = gateway().assume(
                conn.role_arn or "", conn.external_id, f"costlens-{conn.org_id}"
            )
            totals = ce_fallback.fetch(
                gateway().client(aws_session, "ce", region="us-east-1"), utcnow().date()
            )
            set_tenant(session, org_id=conn.org_id)
            ce_fallback.apply(session, totals)
        return sum(counts.values())


@job("inventory.schedule", queue="maintenance", cron="45 */6 * * *")
def schedule_inventory() -> int:
    from costlens.jobs.runner import build_runner

    ctx = job_context()
    with ctx.db.session() as session:
        set_tenant(session, bypass=True)
        ids = session.scalars(
            select(CloudConnection.id).where(
                CloudConnection.provider == "aws", CloudConnection.status == "active"
            )
        ).all()
    runner = build_runner(ctx.settings)
    for connection_id in ids:
        runner.enqueue("inventory.collect", connection_id=str(connection_id))
    return len(ids)


@job("costs.recompute_virtual_tags", queue="default", retry=1)
def recompute_virtual_tags(org_id: str) -> int:
    from costlens.costs.vtags import recompute

    ctx = job_context()
    with ctx.db.session() as session:
        set_tenant(session, org_id=uuid.UUID(org_id))
        return recompute(session, uuid.UUID(org_id))
