"""Send alerts to Slack and Microsoft Teams incoming webhooks.

Webhook addresses are limited to the providers' own hosts and checked against the
SSRF guard, so a channel can never be pointed at an internal service."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ccradar.core.netguard import Resolver, UnsafeURLError, check_public_https_url, system_resolver
from ccradar.core.redact import redact
from ccradar.db.base import utcnow
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


def payload(kind: str, m: Message) -> dict[str, Any]:
    text = redact(m.text)
    if kind == "slack":
        blocks: list[dict[str, Any]] = [
            {"type": "header", "text": {"type": "plain_text", "text": m.title[:150]}},
            {"type": "section", "text": {"type": "mrkdwn", "text": text[:2900]}},
        ]
        if m.link:
            blocks.append(
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Open in CloudCostRadar"},
                            "url": m.link,
                        }
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
    if m.link:
        card["actions"] = [
            {"type": "Action.OpenUrl", "title": "Open in CloudCostRadar", "url": m.link}
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
        response = client.post(channel.url, json=payload(channel.kind, m), timeout=10.0)
        response.raise_for_status()
    except (WebhookError, httpx.HTTPError) as error:
        channel.last_error = f"{type(error).__name__}: {str(error)[:400]}"
        log.warning("notification to %s channel failed: %s", channel.kind, type(error).__name__)
        return False
    channel.last_sent_at, channel.last_error = utcnow(), None
    return True


def notify(db: Session, event: str, messages: Iterable[Message]) -> int:
    """The one notification service: the in-app inbox (and personal email) of the people
    concerned, every enabled channel subscribed to ``event``, and outgoing webhooks."""
    messages = list(messages)
    from ccradar.core.inbox import deliver

    deliver(db, event, messages)  # the in-app inbox and personal email (9.4)
    channels = [
        c
        for c in db.scalars(
            select(NotificationChannel).where(NotificationChannel.enabled.is_(True))
        )
        if event in (c.events or [])
    ]
    sent = 0
    for m in messages:
        for c in channels:
            sent += send(c, m)
    from ccradar.core.webhooks_out import enqueue

    enqueue(db, event, messages)
    db.flush()
    return sent
