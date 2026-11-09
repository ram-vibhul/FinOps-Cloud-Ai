"""The notification centre (9.4): every notification lands in the inbox of the people
it concerns, and each person chooses per kind whether it shows in their inbox and
whether it is also emailed to them.

``notify()`` calls :func:`deliver`, so the same event reaches the in-app inbox,
personal email, Slack/Teams channels and outgoing webhooks from one place."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from costlens.accounts.roles import Role
from costlens.db.tenant import current_tenant, set_tenant
from costlens.models import InboxItem, Membership, User

if TYPE_CHECKING:
    from costlens.core.notify import Message

log = logging.getLogger(__name__)
Audience = Literal["all", "admins", "self"]

# kind -> (label shown in preferences, who receives it)
EVENTS: dict[str, tuple[str, Audience]] = {
    "cost_anomaly": ("Unusual cost increases", "all"),
    "budget_alert": ("Budget alerts", "all"),
    "early_warning": ("Early warnings of overspend", "all"),
    "weekly_digest": ("Weekly digest", "all"),
    "saving_regressed": ("Savings that slipped back", "all"),
    "commitment_expiring": ("Commitments about to expire", "all"),
    "policy_violation": ("Policy breaches", "all"),
    "k8s_incident": ("Kubernetes incidents", "all"),
    "first_savings": ("First savings found", "all"),
    "import_finished": ("Cost imports finished", "all"),
    "import_failed": ("Cost imports failed", "admins"),
    "billing": ("Plan and billing changes", "admins"),
    "purchase_planned": ("Purchases waiting for approval", "admins"),
    "fix_request": ("Fixes waiting for approval", "admins"),
    "access_request": ("Access requests waiting", "admins"),
    "invitation": ("Invitations to join an organisation", "self"),
}
ADMIN_ROLES = (Role.ADMIN, Role.OWNER)
MAX_PER_EVENT = 10  # a burst of alerts becomes at most this many inbox rows each


def _recipients(
    db: Session, org_id: uuid.UUID, audience: Audience, user_ids: Iterable[uuid.UUID] | None
) -> list[tuple[Membership | None, User]]:
    if user_ids is not None:
        ids = list(user_ids)
        users = db.scalars(select(User).where(User.id.in_(ids))).all() if ids else []
        members = {
            m.user_id: m
            for m in db.scalars(
                select(Membership).where(Membership.org_id == org_id, Membership.user_id.in_(ids))
            )
        }
        return [(members.get(u.id), u) for u in users]
    q = select(Membership, User).join(User, User.id == Membership.user_id)
    q = q.where(Membership.org_id == org_id)
    if audience == "admins":
        q = q.where(Membership.role.in_(ADMIN_ROLES))
    return [(m, u) for m, u in db.execute(q).all()]


def _base_url() -> str:
    from costlens.jobs.context import job_context

    try:
        return job_context().settings.base_url.rstrip("/")
    except RuntimeError:
        return ""


def queue_email(to: str, subject: str, text: str) -> None:
    """Queue one plain-text email (sent by the ``email.send`` job)."""
    from costlens.core.email import Email
    from costlens.jobs.context import job_context
    from costlens.jobs.runner import build_runner

    try:
        runner = build_runner(job_context().settings)
    except RuntimeError:  # no job context (scripts): the inbox still has it
        log.info("notification email to %s not queued: jobs are not configured", to)
        return
    runner.enqueue(
        "email.send",
        message=Email(to=to, subject=subject, text=text, tags={"kind": "notification"}).to_dict(),
    )


def deliver(
    db: Session,
    event: str,
    messages: Iterable[Message],
    *,
    user_ids: Iterable[uuid.UUID] | None = None,
) -> int:
    """Put ``messages`` in the inbox of everyone in the current organisation the event
    concerns (or only ``user_ids``), and email those who asked for it. Returns rows added."""
    org_id = current_tenant(db).org_id
    if org_id is None or event not in EVENTS:
        return 0
    messages = list(messages)[:MAX_PER_EVENT]
    if not messages:
        return 0
    added = 0
    for member, user in _recipients(db, org_id, EVENTS[event][1], user_ids):
        prefs = (member.notification_prefs if member else None) or {}
        if event not in prefs.get("inbox_off", []):
            for m in messages:
                db.add(
                    InboxItem(
                        org_id=org_id,
                        user_id=user.id,
                        event=event,
                        title=m.title[:200],
                        text=(m.text or "")[:2000],
                        link=(m.link or None) and m.link[:500],
                        severity=m.severity,
                    )
                )
                added += 1
        if event in prefs.get("email", []) and user.email:
            for m in messages:
                link = m.link or ""
                if link.startswith("/"):
                    link = _base_url() + link
                text = m.text + (f"\n\nOpen: {link}" if link else "")
                text += "\n\nChange what you are sent under Notifications in CostLens."
                queue_email(user.email, f"CostLens: {m.title}", text)
    db.flush()
    return added


@contextmanager
def as_owner_of_items(db: Session) -> Iterator[None]:
    """A person's inbox spans organisations; reads and updates are always filtered by
    ``user_id``, so the tenant scope is lifted only for the duration of the call."""
    before = current_tenant(db)
    set_tenant(db, bypass=True)
    try:
        yield
    finally:
        set_tenant(db, org_id=before.org_id, user_id=before.user_id, bypass=before.bypass)
