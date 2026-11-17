"""Send alerts to Slack and Microsoft Teams incoming webhooks.

Webhook addresses are limited to the providers' own hosts and checked against the
SSRF guard, so a channel can never be pointed at an internal service."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from sqlalchemy import event as sa_event
from sqlalchemy import select
from sqlalchemy.orm import Session

from ccradar.core.netguard import Resolver, UnsafeURLError, check_public_https_url, system_resolver
from ccradar.core.redact import redact
from ccradar.db.base import utcnow
from ccradar.db.tenant import current_tenant
from ccradar.models import NotificationChannel

log = logging.getLogger(__name__)
Kind = Literal["slack", "teams"]
EVENTS = (
    "k8s_incident",
    "commitment_expiring",
    "purchase_planned",
    "saving_regressed",
    "budget_alert",
    "policy_violation",
    "early_warning",
    "cost_anomaly",
    "weekly_digest",
    "access_request",
    "fix_request",
    "import_finished",
    "import_failed",
    "first_savings",
    "billing",
    "live_change",
)
HOSTS: dict[str, tuple[str, ...]] = {
    "slack": ("hooks.slack.com",),
    # Teams Workflows (Power Automate) and the older Office 365 connectors.
    "teams": (
        ".webhook.office.com",
        ".logic.azure.com",
        ".powerplatform.com",
        ".powerautomate.com",
    ),
}

_http: httpx.Client | None = None
_resolver: Resolver = system_resolver


def configure(http: httpx.Client, resolver: Resolver) -> None:
    global _http, _resolver
    _http, _resolver = http, resolver


def outbound() -> tuple[httpx.Client, Resolver]:
    """The HTTP client and DNS resolver set at startup (faked in tests)."""
    return _http or httpx.Client(timeout=10.0), _resolver


class WebhookError(ValueError):
    pass


def validate_webhook(kind: str, url: str, resolver: Resolver | None = None) -> str:
    host = (urlsplit(url).hostname or "").lower()
    allowed = HOSTS.get(kind, ())
    if not any(host == h or (h.startswith(".") and host.endswith(h)) for h in allowed):
        names = {"slack": "hooks.slack.com", "teams": "a Teams Workflows or webhook.office.com"}
        raise WebhookError(f"Use an incoming webhook address from {names.get(kind, kind)}.")
    try:
        return check_public_https_url(url, resolver or _resolver)
    except UnsafeURLError as exc:
        raise WebhookError(f"This webhook address cannot be used: {exc}.") from exc


@dataclass
class Message:
    title: str
    text: str  # plain text, short lines
    link: str | None = None
    severity: Literal["alert", "resolved", "info"] = "alert"
    # Extra link buttons in Slack and Teams, as (label, link) pairs (11.31).
    actions: list[tuple[str, str]] = field(default_factory=list)


def _absolute(link: str | None, base_url: str) -> str | None:
    """Slack and Teams refuse relative links in buttons; app links start with "/"."""
    if not link:
        return None
    if link.startswith("/"):
        return base_url.rstrip("/") + link if base_url.startswith(("https://", "http://")) else None
    return link if link.startswith(("https://", "http://")) else None


def buttons(m: Message, base_url: str) -> list[tuple[str, str]]:
    """The message's buttons with absolute links; one without a usable link is left out."""
    wanted = [*([("Open in CloudCostRadar", m.link)] if m.link else []), *m.actions]
    out = []
    for label, link in wanted:
        url = _absolute(link, base_url)
        if url:
            out.append((label, url))
    return out[:5]


def payload(kind: str, m: Message, base_url: str = "") -> dict[str, Any]:
    text = redact(m.text)
    links = buttons(m, base_url)
    if kind == "slack":
        blocks: list[dict[str, Any]] = [
            {"type": "header", "text": {"type": "plain_text", "text": m.title[:150]}},
            {"type": "section", "text": {"type": "mrkdwn", "text": text[:2900]}},
        ]
        if links:
            blocks.append(
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": label[:75]},
                            "url": url,
                        }
                        for label, url in links
                    ],
                }
            )
        return {"text": f"{m.title}\n{text}"[:3000], "blocks": blocks}
    color = {"alert": "Attention", "resolved": "Good", "info": "Default"}[m.severity]
    card: dict[str, Any] = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": [
            {
                "type": "TextBlock",
                "text": m.title,
                "weight": "Bolder",
                "size": "Medium",
                "color": color,
                "wrap": True,
            },
            *(
                {"type": "TextBlock", "text": line, "wrap": True, "spacing": "Small"}
                for line in text.splitlines()
                if line.strip()
            ),
        ],
    }
    if links:
        card["actions"] = [
            {"type": "Action.OpenUrl", "title": label, "url": url} for label, url in links
        ]
    return {
        "type": "message",
        "attachments": [
            {"contentType": "application/vnd.microsoft.card.adaptive", "content": card}
        ],
    }


def send(channel: NotificationChannel, m: Message, http: httpx.Client | None = None) -> bool:
    client = http or _http or httpx.Client(timeout=10.0)
    try:
        validate_webhook(channel.kind, channel.url)  # DNS may have changed since saving
        from ccradar.core.inbox import _base_url

        body = payload(channel.kind, m, _base_url())
        response = client.post(channel.url, json=body, timeout=10.0)
        response.raise_for_status()
    except (WebhookError, httpx.HTTPError) as error:
        channel.last_error = f"{type(error).__name__}: {str(error)[:400]}"
        log.warning("notification to %s channel failed: %s", channel.kind, type(error).__name__)
        return False
    channel.last_sent_at, channel.last_error = utcnow(), None
    return True


FAN_OUT_JOB = "notifications.fan_out"
PENDING_KEY = "ccradar.pending_notifications"


def _background_runner() -> Any:
    """The job runner when jobs run in a separate worker; None when they run in this
    process (SQLite, tests, scripts), where deferring would gain nothing."""
    from ccradar.jobs.context import job_context
    from ccradar.jobs.runner import build_runner

    try:
        settings = job_context().settings
    except RuntimeError:
        return None
    return None if settings.run_jobs_in_process else build_runner(settings)


def _channels(db: Session, event: str) -> list[NotificationChannel]:
    return [
        c
        for c in db.scalars(
            select(NotificationChannel).where(NotificationChannel.enabled.is_(True))
        )
        if event in (c.events or [])
    ]


def notify(db: Session, event: str, messages: Iterable[Message]) -> int:
    """The one notification service: the in-app inbox (and personal email) of the people
    concerned, every enabled channel subscribed to ``event``, and outgoing webhooks.

    With a separate worker (PostgreSQL) the delivery runs in a background job queued
    when this transaction commits (11.13): a large organisation means hundreds of inbox
    rows and emails, and Slack or Teams can take seconds to answer, which should hold
    up neither a web request nor the job that found the problem. Nothing is sent if the
    transaction rolls back. Returns the channel posts sent, or queued to be sent."""
    messages = list(messages)
    if not messages:
        return 0
    org_id = current_tenant(db).org_id
    if org_id is not None and _background_runner() is not None:
        db.info.setdefault(PENDING_KEY, []).append(
            {"org_id": str(org_id), "event": event, "messages": [asdict(m) for m in messages]}
        )
        return len(messages) * len(_channels(db, event))
    return fan_out(db, event, messages)


@sa_event.listens_for(Session, "after_commit")
def _queue_committed(db: Session) -> None:
    pending = db.info.pop(PENDING_KEY, None)
    if not pending:
        return
    runner = _background_runner()
    for job in pending:
        try:
            runner.enqueue(FAN_OUT_JOB, **job)
        except Exception:  # the change is saved; a lost alert must not fail the request
            log.exception("could not queue %s notifications", job["event"])


@sa_event.listens_for(Session, "after_rollback")
def _drop_rolled_back(db: Session) -> None:
    db.info.pop(PENDING_KEY, None)


def fan_out(db: Session, event: str, messages: list[Message]) -> int:
    """Deliver now, in the current organisation: see :func:`notify`."""
    from ccradar.core.inbox import deliver

    deliver(db, event, messages)  # the in-app inbox and personal email (9.4)
    channels = _channels(db, event)
    sent = 0
    for m in messages:
        for c in channels:
            sent += send(c, m)
    from ccradar.core.webhooks_out import enqueue

    enqueue(db, event, messages)
    db.flush()
    return sent
