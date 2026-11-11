"""Background email delivery."""

from __future__ import annotations

from typing import Any

from ccradar.core.email import Email, email_sender
from ccradar.jobs.registry import job


@job("email.send", queue="email", retry=5)
def send_email(message: dict[str, Any]) -> None:
    email_sender().send(Email.from_dict(message))


@job("reports.send_due", queue="email", cron="5 * * * *")
def send_due_reports() -> int:
    """Every hour: email the scheduled reports that are due (from 07:00 UTC)."""
    from sqlalchemy import select

    from ccradar.db.tenant import set_tenant
    from ccradar.jobs.context import job_context
    from ccradar.models import Organization
    from ccradar.reports.scheduled import send_due

    def deliver(message: dict[str, Any]) -> None:
        email_sender().send(Email.from_dict(message))

    ctx = job_context()
    with ctx.db.session() as session:
        set_tenant(session, bypass=True)
        orgs = list(session.scalars(select(Organization.id).where(Organization.is_demo.is_(False))))
    sent = 0
    for org_id in orgs:
        with ctx.db.session() as session:
            set_tenant(session, org_id=org_id)
            sent += send_due(session, org_id, deliver)
    return sent
