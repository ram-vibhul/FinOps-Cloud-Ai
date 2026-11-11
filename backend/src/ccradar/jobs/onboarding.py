"""First savings fast (9.5): right after a cloud is connected, import its costs, read the
cloud's own recommendations and run every detector, instead of waiting for the nightly
jobs. People are told when the first savings are ready."""

from __future__ import annotations

import logging
import uuid

from ccradar.db.tenant import set_tenant
from ccradar.jobs.context import job_context
from ccradar.jobs.registry import job

log = logging.getLogger(__name__)


def _import(connection_id: str, provider: str, has_bucket: bool) -> None:
    from ccradar.jobs import costs

    if provider == "aws":
        if has_bucket:
            costs.import_cur(connection_id, months=3)
        else:
            costs.import_cost_explorer(connection_id, months=2)
    elif provider in ("azure", "gcp"):
        getattr(costs, f"import_{provider}")(connection_id, months=3)
    else:
        costs.import_saas(connection_id, months=3)


@job("onboarding.first_run", queue="imports", retry=0)
def first_run(connection_id: str) -> int:
    """Import, advice, detectors and a notification for a newly connected cloud."""
    from ccradar.core.notify import Message, notify
    from ccradar.findings import first_savings
    from ccradar.findings.service import run
    from ccradar.jobs.findings import advice_for
    from ccradar.models import CloudConnection, Organization

    ctx = job_context()
    with ctx.db.session() as session:
        set_tenant(session, bypass=True)
        conn = session.get(CloudConnection, uuid.UUID(connection_id))
        if conn is None:
            return 0
        org_id, provider, has_bucket = conn.org_id, conn.provider, bool(conn.cur_bucket)
    steps = (
        ("import", lambda: _import(connection_id, provider, has_bucket)),
        ("advice", lambda: advice_for(uuid.UUID(connection_id), provider)),
    )
    for name, step in steps:
        try:
            step()
        except Exception:  # one failing step must not stop the others
            log.exception("first run for %s: %s failed", connection_id, name)
    with ctx.db.session() as session:
        set_tenant(session, org_id=org_id)
        try:
            run(session, org_id)
        except Exception:  # still finish, so onboarding never stays "in progress"
            log.exception("first run for %s: detectors failed", connection_id)
        org = session.get(Organization, org_id)
        if org is None:
            return 0
        found = first_savings.summary(session, org)
        first_savings.mark(org, "done", found=found["count"])
        if found["count"]:
            money = f"{found['savings_monthly']:,.0f} {found['currency']}"
            message = Message(
                title=f"First savings ready: about {money} a month",
                text="\n".join(f"• {t['title']}" for t in found["top"]),
                link="/savings",
                severity="info",
            )
            notify(session, "first_savings", [message])
        return int(found["count"])
