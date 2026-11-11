"""Outgoing webhooks.

Every event that goes to Slack/Teams channels (``notify``) is also queued for each
enabled webhook subscribed to it. A job sends what is due every minute.

- Body: JSON ``{"id", "event", "created_at", "org_id", "data": {title, text, link,
  severity}}``.
- Headers: ``X-CloudCostRadar-Event``, ``X-CloudCostRadar-Delivery`` (the id; receivers de-duplicate
  on it), and ``X-CloudCostRadar-Signature: t=<unix seconds>,v1=<hex>``. The hex is
  HMAC-SHA256 with the webhook secret over ``"<t>.<raw body>"``. Receivers should reject
  timestamps older than 5 minutes.
- Only public HTTPS addresses are allowed: the SSRF guard resolves DNS and blocks
  private ranges, both when saving and before every attempt. Redirects are not followed.
- Retries: a 2xx answer means delivered. Otherwise retried after 1 min, 5 min, 30 min,
  2 h and 6 h, then marked failed."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import uuid
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ccradar.core.netguard import Resolver, UnsafeURLError, check_public_https_url
from ccradar.core.redact import redact
from ccradar.db.base import utcnow
from ccradar.models import OutgoingWebhook, WebhookDelivery

log = logging.getLogger(__name__)
BACKOFF = [
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=30),
    timedelta(hours=2),
    timedelta(hours=6),
]
MAX_ATTEMPTS = len(BACKOFF) + 1
TIMEOUT = 10.0
BATCH = 200


def new_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(32)


def sign(secret: str, body: bytes, timestamp: int) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={mac}"


def verify(secret: str, body: bytes, header: str, now: int, tolerance: int = 300) -> bool:
    """What a receiver does; used by tests and documented for customers."""
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    try:
        ts = int(parts["t"])
    except (KeyError, ValueError):
        return False
    if abs(now - ts) > tolerance:
        return False
    return hmac.compare_digest(sign(secret, body, ts), f"t={ts},v1={parts.get('v1', '')}")


def enqueue(db: Session, event: str, messages: Iterable[Any]) -> int:
    """Queue one delivery per (webhook, message) for webhooks subscribed to ``event``."""
    hooks = [
        h
        for h in db.scalars(select(OutgoingWebhook).where(OutgoingWebhook.enabled.is_(True)))
        if event in (h.events or [])
    ]
    if not hooks:
        return 0
    now = utcnow()
    n = 0
    for m in messages:
        data = {
            "title": m.title,
            "text": redact(m.text),
            "link": m.link,
            "severity": m.severity,
        }
        for h in hooks:
            db.add(_delivery(h, event, data, now))
            n += 1
    db.flush()
    return n


def _delivery(
    h: OutgoingWebhook, event: str, data: dict[str, Any], now: datetime
) -> WebhookDelivery:
    did = uuid.uuid4()
    return WebhookDelivery(
        id=did,
        org_id=h.org_id,
        webhook_id=h.id,
        event=event,
        payload={
            "id": str(did),
            "event": event,
            "created_at": now.isoformat(),
            "org_id": str(h.org_id),
            "data": data,
        },
        next_attempt_at=now,
    )


def ping(db: Session, h: OutgoingWebhook) -> WebhookDelivery:
    d = _delivery(
        h,
        "ping",
        {
            "title": "CloudCostRadar test",
            "text": "This webhook works.",
            "link": None,
            "severity": "info",
        },
        utcnow(),
    )
    db.add(d)
    db.flush()
    return d


def attempt(
    db: Session,
    d: WebhookDelivery,
    http: httpx.Client,
    resolver: Resolver,
    now: datetime | None = None,
) -> bool:
    now = now or utcnow()
    h = db.get(OutgoingWebhook, d.webhook_id)
    if h is None:
        return False
    body = json.dumps(d.payload, separators=(",", ":"), sort_keys=True).encode()
    d.attempts += 1
    status: int | None = None
    error: str | None = None
    try:
        check_public_https_url(h.url, resolver)
        r = http.post(
            h.url,
            content=body,
            timeout=TIMEOUT,
            follow_redirects=False,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "CloudCostRadar-Webhooks/1",
                "X-CloudCostRadar-Event": d.event,
                "X-CloudCostRadar-Delivery": str(d.id),
                "X-CloudCostRadar-Signature": sign(h.secret, body, int(now.timestamp())),
            },
        )
        status = r.status_code
        if not 200 <= status < 300:
            error = f"answered {status}"
    except UnsafeURLError as exc:
        error = f"address not allowed: {exc}"
    except httpx.HTTPError as exc:
        error = f"{type(exc).__name__}"
    d.last_status, d.last_error = status, error
    h.last_sent_at, h.last_status, h.last_error = now, status, error
    if error is None:
        d.status, d.delivered_at, d.next_attempt_at = "sent", now, None
    elif d.attempts >= MAX_ATTEMPTS:
        d.status, d.next_attempt_at = "failed", None
        log.warning("webhook delivery %s failed after %s attempts", d.id, d.attempts)
    else:
        d.next_attempt_at = now + BACKOFF[d.attempts - 1]
    db.flush()
    return error is None


def deliver_due(
    db: Session, http: httpx.Client, resolver: Resolver, now: datetime | None = None
) -> int:
    now = now or utcnow()
    due = db.scalars(
        select(WebhookDelivery)
        .where(WebhookDelivery.status == "pending", WebhookDelivery.next_attempt_at <= now)
        .order_by(WebhookDelivery.next_attempt_at)
        .limit(BATCH)
    ).all()
    return sum(attempt(db, d, http, resolver, now) for d in due)
