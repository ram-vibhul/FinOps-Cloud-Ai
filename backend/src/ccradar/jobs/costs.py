"""Cost import jobs."""

from __future__ import annotations

import functools
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import select

from ccradar.clouds.aws import cost_explorer, cur
from ccradar.clouds.aws.client import AwsGateway, Boto3Gateway
from ccradar.db.tenant import set_tenant
from ccradar.jobs.context import job_context
from ccradar.jobs.registry import job
from ccradar.models import CloudConnection

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
    from ccradar.jobs.runner import build_runner

    build_runner(job_context().settings).enqueue("costs.detect_anomalies", org_id=str(org_id))


def _connection_state(connection_id: str) -> tuple[bool, bool] | None:
    """(had an error, had imported before) for a connection, or None if it is gone."""
    with job_context().db.session() as session:
        set_tenant(session, bypass=True)
        conn = session.get(CloudConnection, uuid.UUID(connection_id))
        return None if conn is None else (bool(conn.last_error), conn.last_checked_at is not None)


def _report_connection(connection_id: str, before: tuple[bool, bool] | None) -> None:
    """Tell people about changes only (9.4): a connection that starts failing, recovers,
    or brings in its first costs. Routine successful imports stay quiet."""
    from ccradar.core.notify import Message, notify

    if before is None:
        return
    had_error, had_import = before
    with job_context().db.session() as session:
        set_tenant(session, bypass=True)
        conn = session.get(CloudConnection, uuid.UUID(connection_id))
        if conn is None:
            return
        set_tenant(session, org_id=conn.org_id)
        link = "/settings?tab=clouds"
        if conn.last_error and not had_error:
            notify(
                session,
                "import_failed",
                [
                    Message(
                        title=f"Cost import failed: {conn.name}",
                        text=f"{conn.last_error[:300]}\nCloudCostRadar will keep trying.",
                        link=link,
                    )
                ],
            )
        elif not conn.last_error and had_error:
            notify(
                session,
                "import_finished",
                [
                    Message(
                        title=f"Cost import working again: {conn.name}",
                        text="The latest costs were imported.",
                        link="/costs",
                        severity="resolved",
                    )
                ],
            )
        elif not conn.last_error and not had_import and conn.last_checked_at is not None:
            notify(
                session,
                "import_finished",
                [
                    Message(
                        title=f"First costs imported from {conn.name}",
                        text="Costs and savings are ready to explore.",
                        link="/costs",
                        severity="info",
                    )
                ],
            )


def _watched[F: Callable[..., Any]](fn: F) -> F:
    """Wrap a connection import job so failures and first imports are notified."""

    @functools.wraps(fn)
    def run(connection_id: str, *args: Any, **kwargs: Any) -> Any:
        before = _connection_state(connection_id)
        try:
            return fn(connection_id, *args, **kwargs)
        finally:
            _report_connection(connection_id, before)

    return run  # type: ignore[return-value]


@job("costs.import_cost_explorer", queue="imports", retry=3)
@_watched
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
@_watched
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
    """Queue imports for every active connection: AWS CUR when configured, else Cost
    Explorer; Azure through its own importer."""
    from ccradar.jobs.runner import build_runner

    ctx = job_context()
    with ctx.db.session() as session:
        set_tenant(session, bypass=True)
        rows = session.execute(
            select(CloudConnection.id, CloudConnection.cur_bucket, CloudConnection.provider).where(
                CloudConnection.status == "active",
            )
        ).all()
    runner = build_runner(ctx.settings)
    for connection_id, bucket, provider in rows:
        if provider in ("azure", "gcp"):
            name = f"costs.import_{provider}"
        elif provider != "aws":
            name = "costs.import_saas"
        else:
            name = "costs.import_cur" if bucket else "costs.import_cost_explorer"
        runner.enqueue(name, connection_id=str(connection_id))
    return len(rows)


@job("costs.import_file", queue="imports", retry=0)
def import_file(import_id: str) -> int:
    from ccradar.costs.files import run_import

    ctx = job_context()
    with ctx.db.session() as session:
        record = run_import(session, ctx.storage, uuid.UUID(import_id))
        org_id, imported = record.org_id, record.status == "imported"
        rows = record.rows_imported
        _report_file(session, record)
    if imported:
        _after_import(org_id)
    return rows


def _report_file(session: Any, record: Any) -> None:
    from ccradar.core.notify import Message, notify

    set_tenant(session, org_id=record.org_id)
    if record.status == "imported":
        message = Message(
            title=f"File imported: {record.filename}",
            text=f"{record.rows_imported:,} rows of costs were added.",
            link="/costs",
            severity="info",
        )
        notify(session, "import_finished", [message])
    else:
        errors = (record.report or {}).get("error_count", 0)
        message = Message(
            title=f"File not imported: {record.filename}",
            text=f"{errors:,} rows could not be read. Open the upload to see why.",
            link="/settings?tab=clouds",
        )
        notify(session, "import_failed", [message])


@job("costs.detect_anomalies", queue="default", retry=1)
def detect_anomalies(org_id: str) -> int:
    """Detect anomalies for one organisation and email owners/admins about new ones."""
    from ccradar.core.email import email_sender
    from ccradar.costs.alerts import pending_alert
    from ccradar.costs.anomalies import detect

    ctx = job_context()
    with ctx.db.session() as session:
        created = detect(session, uuid.UUID(org_id))
        emails = pending_alert(session, uuid.UUID(org_id), ctx.settings.base_url)
    for message in emails:
        email_sender().send(message)
    return len(created)


@job("costs.detect_all_anomalies", queue="maintenance", cron="30 7 * * *")
def detect_all_anomalies() -> int:
    from ccradar.jobs.runner import build_runner
    from ccradar.models import CostDaily

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
    from ccradar.changes.cloudtrail import import_connection

    ctx = job_context()
    with ctx.db.session() as session:
        return import_connection(session, gateway(), uuid.UUID(connection_id))


@job("changes.schedule_cloudtrail", queue="maintenance", cron="5 * * * *")
def schedule_cloudtrail() -> int:
    from ccradar.jobs.runner import build_runner

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
    from ccradar.costs.tagrules import reapply
    from ccradar.db.tenant import set_tenant as scope

    ctx = job_context()
    with ctx.db.session() as session:
        scope(session, org_id=uuid.UUID(org_id))
        return reapply(session, uuid.UUID(org_id))


@job("inventory.collect", queue="imports", retry=1)
def collect_inventory(connection_id: str) -> int:
    from ccradar.inventory.service import collect_aws

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
            from ccradar.db.base import utcnow
            from ccradar.inventory import ce_fallback

            aws_session = gateway().assume(
                conn.role_arn or "", conn.external_id, f"ccradar-{conn.org_id}"
            )
            totals = ce_fallback.fetch(
                gateway().client(aws_session, "ce", region="us-east-1"), utcnow().date()
            )
            set_tenant(session, org_id=conn.org_id)
            ce_fallback.apply(session, totals)
        return sum(counts.values())


@job("inventory.schedule", queue="maintenance", cron="45 */6 * * *")
def schedule_inventory() -> int:
    from ccradar.jobs.runner import build_runner

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
    from ccradar.costs.vtags import recompute

    ctx = job_context()
    with ctx.db.session() as session:
        set_tenant(session, org_id=uuid.UUID(org_id))
        return recompute(session, uuid.UUID(org_id))


@job("budgets.check", queue="maintenance", cron="10 8 * * *")
def check_budgets() -> int:
    """Budget thresholds on spend and forecast (after the morning imports)."""
    from ccradar.core.email import Email, email_sender
    from ccradar.costs.budgets import check_alerts
    from ccradar.models import Organization

    def send(_name: str, message: dict) -> None:
        email_sender().send(Email.from_dict(message))

    ctx = job_context()
    with ctx.db.session() as session:
        set_tenant(session, bypass=True)
        orgs = list(session.scalars(select(Organization.id).where(Organization.is_demo.is_(False))))
    sent = 0
    for org_id in orgs:
        with ctx.db.session() as session:
            set_tenant(session, org_id=org_id)
            sent += check_alerts(session, org_id, enqueue=send)
    return sent


@job("governance.evaluate", queue="maintenance", cron="20 8 * * *")
def evaluate_policies() -> int:
    """Re-check every governance policy (after inventory and imports)."""
    from ccradar.costs.governance import evaluate
    from ccradar.models import Organization

    ctx = job_context()
    with ctx.db.session() as session:
        set_tenant(session, bypass=True)
        orgs = [
            (o.id, o.default_currency)
            for o in session.scalars(select(Organization).where(Organization.is_demo.is_(False)))
        ]
    opened = 0
    for org_id, currency in orgs:
        with ctx.db.session() as session:
            set_tenant(session, org_id=org_id)
            opened += evaluate(session, org_id, currency)["new"]
    return opened


@job("costs.early_warnings", queue="maintenance", cron="20 * * * *")
def early_warnings() -> int:
    """Every hour: forecast overspend (once a month) and same-day spend (once a day)."""
    from ccradar.costs.early_warnings import check
    from ccradar.models import Organization

    ctx = job_context()
    with ctx.db.session() as session:
        set_tenant(session, bypass=True)
        orgs = list(session.scalars(select(Organization.id).where(Organization.is_demo.is_(False))))
    sent = 0
    for org_id in orgs:
        with ctx.db.session() as session:
            set_tenant(session, org_id=org_id)
            sent += check(session, org_id)
    return sent


@job("costs.weekly_digest", queue="maintenance", cron="0 8 * * 1")
def weekly_digest() -> int:
    """Monday 08:00: last week's spend to digest channels (and email when turned on)."""
    from ccradar.costs.digest import send
    from ccradar.jobs.runner import build_runner
    from ccradar.models import Organization

    ctx = job_context()
    with ctx.db.session() as session:
        set_tenant(session, bypass=True)
        orgs = list(session.scalars(select(Organization.id).where(Organization.is_demo.is_(False))))
    runner = build_runner(ctx.settings)
    sent = 0
    for org_id in orgs:
        with ctx.db.session() as session:
            set_tenant(session, org_id=org_id)
            sent += send(session, org_id, ctx.settings.base_url, enqueue=runner.enqueue)
    return sent


@job("webhooks.deliver_due", queue="maintenance", cron="* * * * *")
def deliver_webhooks() -> int:
    """Every minute: send outgoing webhook deliveries that are due (first tries and retries)."""
    from ccradar.core.notify import outbound
    from ccradar.core.webhooks_out import deliver_due

    http, resolver = outbound()
    ctx = job_context()
    with ctx.db.session() as session:
        set_tenant(session, bypass=True)
        return deliver_due(session, http, resolver)


@job("costs.import_azure", queue="imports", retry=3)
@_watched
def import_azure(connection_id: str, months: int = 2) -> int:
    """Azure costs for one connection: the FOCUS export when set, else Cost Management."""
    from ccradar.clouds.azure import costs as azure_costs
    from ccradar.clouds.azure.client import for_connection

    ctx = job_context()
    with ctx.db.session() as session:
        set_tenant(session, bypass=True)
        conn = session.get(CloudConnection, uuid.UUID(connection_id))
        if conn is None:
            return 0
        runs = azure_costs.import_connection(session, for_connection(conn), conn.id, months=months)
        rows, org_id = sum(r.rows for r in runs), (runs[0].org_id if runs else None)
    if org_id:
        _after_import(org_id)
    return rows


@job("costs.import_gcp", queue="imports", retry=3)
@_watched
def import_gcp(connection_id: str, months: int = 2) -> int:
    """Google Cloud costs for one connection from its BigQuery billing export."""
    from ccradar.clouds.gcp import client as gcp_client
    from ccradar.clouds.gcp import costs as gcp_costs

    ctx = job_context()
    with ctx.db.session() as session:
        set_tenant(session, bypass=True)
        conn = session.get(CloudConnection, uuid.UUID(connection_id))
        if conn is None:
            return 0
        runs = gcp_costs.import_connection(
            session, gcp_client.for_connection(conn), conn.id, months=months
        )
        rows, org_id = sum(r.rows for r in runs), (runs[0].org_id if runs else None)
    if org_id:
        _after_import(org_id)
    return rows


@job("costs.import_saas", queue="imports", retry=3)
@_watched
def import_saas(connection_id: str, months: int = 2) -> int:
    """Snowflake, Databricks, Datadog or MongoDB Atlas costs for one connection."""
    from ccradar.connectors.saas import import_connection
    from ccradar.core.notify import outbound

    ctx = job_context()
    with ctx.db.session() as session:
        runs = import_connection(session, outbound()[0], uuid.UUID(connection_id), months=months)
        rows, org_id = sum(r.rows for r in runs), (runs[0].org_id if runs else None)
    if org_id:
        _after_import(org_id)
    return rows


@job("costs.import_bedrock_logs", queue="imports", cron="40 5 * * *")
def import_bedrock_logs() -> int:
    """Daily: Bedrock usage per app and team from invocation logs, where configured."""
    from ccradar.clouds.aws.bedrock_logs import import_logs

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
            if ((c.settings or {}).get("bedrock_logs") or {}).get("bucket")
        ]
    events = 0
    for connection_id in ids:
        with ctx.db.session() as session:
            events += import_logs(session, gateway(), connection_id).get("events", 0)
    return events


@job("access.expire_elevations", queue="maintenance", cron="*/5 * * * *")
def expire_elevations() -> int:
    """Every 5 minutes: mark lapsed temporary access as expired (it already stopped working)."""
    from ccradar.accounts.elevation import expire

    ctx = job_context()
    with ctx.db.session() as session:
        set_tenant(session, bypass=True)
        return expire(session)
