"""Subscription periods (B.2)."""

from __future__ import annotations

from ccradar.db.tenant import set_tenant
from ccradar.jobs.context import job_context
from ccradar.jobs.registry import job


@job("billing.roll_periods", queue="maintenance", cron="25 * * * *")
def roll_periods() -> dict[str, int]:
    """Hourly: end trials and cancelled subscriptions, apply scheduled downgrades, renew
    invoiced ones, and follow up failed payments (B.16)."""
    from ccradar.billing.subscriptions import roll

    ctx = job_context()
    with ctx.db.session() as session:
        set_tenant(session, bypass=True)
        return roll(session, grace_days=ctx.settings.payment_grace_days)


@job("usage.snapshot", queue="maintenance", cron="30 0 * * *")
def usage_snapshot() -> int:
    """Nightly: yesterday's usage of every organisation (B.12)."""
    from ccradar.billing.metering import snapshot_all

    with job_context().db.session() as session:
        set_tenant(session, bypass=True)
        return snapshot_all(session)
