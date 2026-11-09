"""Payment provider webhooks (B.5): verify the signature, drop duplicates, and apply what
the event means to the subscription.

Signatures follow each provider's published scheme, checked by hand with the standard
library (no provider SDK needed):

- Razorpay: ``X-Razorpay-Signature`` = hex HMAC-SHA256 of the raw body with the webhook
  secret; the event id is in ``X-Razorpay-Event-Id``.
- Stripe: ``Stripe-Signature: t=<unix time>,v1=<hex>`` where the signature is HMAC-SHA256
  of ``"<t>.<raw body>"``; deliveries older than five minutes are refused (replays).

Every event is stored once per (provider, event id); a repeated delivery is answered
"duplicate" and changes nothing. The organisation and plan of a new subscription come
from the checkout's notes/metadata (``org_id``, ``plan``, ``interval``), which the
provider integrations (B.3, B.4) set when they open a checkout."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from costlens.billing import subscriptions
from costlens.billing.plans import Catalogue
from costlens.db.base import utcnow
from costlens.db.tenant import set_tenant
from costlens.models import Organization, PaymentEvent, Subscription

log = logging.getLogger(__name__)
STRIPE_TOLERANCE_SECONDS = 300
MAX_BODY = 1024 * 1024


class SignatureError(ValueError):
    pass


@dataclass
class Event:
    provider: str
    event_id: str
    event_type: str
    kind: str  # activated | renewed | payment_failed | cancelled | ignored
    subscription_ref: str | None = None
    org_id: str | None = None
    plan: str | None = None
    interval: str = "monthly"
    period_end: datetime | None = None
    occurred_at: datetime | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _ts(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value), UTC) if value else None
    except (TypeError, ValueError):
        return None


# --- Razorpay ---------------------------------------------------------------------------

RAZORPAY_KINDS = {
    "subscription.activated": "activated",
    "subscription.charged": "renewed",
    "subscription.pending": "payment_failed",
    "subscription.halted": "payment_failed",
    "subscription.cancelled": "cancelled",
    "subscription.completed": "cancelled",
}


def verify_razorpay(secret: str, body: bytes, signature: str | None) -> None:
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not signature or not hmac.compare_digest(signature, expected):
        raise SignatureError("bad signature")


def parse_razorpay(body: bytes, event_id: str | None) -> Event:
    data = json.loads(body)
    kind = RAZORPAY_KINDS.get(data.get("event", ""), "ignored")
    entity = ((data.get("payload") or {}).get("subscription") or {}).get("entity") or {}
    notes = entity.get("notes") or {}
    return Event(
        provider="razorpay",
        event_id=event_id or f"{data.get('event')}:{entity.get('id')}:{data.get('created_at')}",
        event_type=str(data.get("event", "")),
        kind=kind,
        subscription_ref=entity.get("id"),
        org_id=notes.get("org_id"),
        plan=notes.get("plan"),
        interval=notes.get("interval") or "monthly",
        period_end=_ts(entity.get("current_end")),
        occurred_at=_ts(data.get("created_at")),
    )


# --- Stripe -----------------------------------------------------------------------------

STRIPE_KINDS = {
    "checkout.session.completed": "activated",
    "invoice.paid": "renewed",
    "invoice.payment_failed": "payment_failed",
    "customer.subscription.deleted": "cancelled",
}


def verify_stripe(
    secret: str, body: bytes, header: str | None, now: datetime | None = None
) -> None:
    parts: dict[str, list[str]] = {}
    for item in (header or "").split(","):
        key, _, value = item.strip().partition("=")
        parts.setdefault(key, []).append(value)
    stamp = (parts.get("t") or [""])[0]
    if not stamp.isdigit() or not parts.get("v1"):
        raise SignatureError("bad signature header")
    age = ((now or utcnow()) - datetime.fromtimestamp(int(stamp), UTC)).total_seconds()
    if abs(age) > STRIPE_TOLERANCE_SECONDS:
        raise SignatureError("too old")
    signed = f"{stamp}.".encode() + body
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(sig, expected) for sig in parts["v1"]):
        raise SignatureError("bad signature")


def parse_stripe(body: bytes) -> Event:
    data = json.loads(body)
    event_type = str(data.get("type", ""))
    obj = (data.get("data") or {}).get("object") or {}
    meta = obj.get("metadata") or {}
    ref = (
        obj.get("id") if event_type.startswith("customer.subscription") else obj.get("subscription")
    )
    period_end = None
    if event_type == "invoice.paid":
        lines = ((obj.get("lines") or {}).get("data")) or [{}]
        period_end = _ts((lines[0].get("period") or {}).get("end")) or _ts(obj.get("period_end"))
    return Event(
        provider="stripe",
        event_id=str(data.get("id", "")),
        event_type=event_type,
        kind=STRIPE_KINDS.get(event_type, "ignored"),
        subscription_ref=ref,
        org_id=meta.get("org_id"),
        plan=meta.get("plan"),
        interval=meta.get("interval") or "monthly",
        period_end=period_end,
        occurred_at=_ts(data.get("created")),
    )


# --- applying ---------------------------------------------------------------------------


def _find(db: Session, provider: str, ref: str | None) -> Subscription | None:
    if not ref:
        return None
    return db.scalar(
        select(Subscription).where(
            Subscription.provider == provider, Subscription.provider_ref == ref
        )
    )


def _apply(db: Session, cat: Catalogue, e: Event) -> tuple[str, uuid.UUID | None]:  # noqa: PLR0911
    from costlens.core.notify import Message, notify

    if e.kind == "ignored":
        return "ignored", None
    if e.kind == "activated":
        try:
            org = db.get(Organization, uuid.UUID(str(e.org_id)))
        except ValueError:
            org = None
        if org is None or cat.get(e.plan) is None or e.interval not in subscriptions.INTERVALS:
            return "missing_checkout_details", None
        sub = subscriptions.activate(
            db, org, str(e.plan), e.interval, provider=e.provider, provider_ref=e.subscription_ref
        )
        if e.period_end:
            sub.current_period_end = e.period_end
        message = Message(
            title="Thank you: your subscription is active",
            text=f"You are on the {cat.plans[str(e.plan)].name} plan.",
            link="/settings?tab=plan",
            severity="info",
        )
        set_tenant(db, org_id=org.id)
        notify(db, "billing", [message])
        set_tenant(db, bypass=True)
        return "applied", org.id
    sub = _find(db, e.provider, e.subscription_ref)
    if sub is None:
        return "unknown_subscription", None
    if e.kind == "renewed":
        if e.period_end and e.period_end > subscriptions._aware(sub.current_period_end):
            sub.current_period_start, sub.current_period_end = sub.current_period_end, e.period_end
        subscriptions.mark_paid(sub)
        return "applied", sub.org_id
    if e.kind == "payment_failed":
        if subscriptions.mark_past_due(sub):  # the first failure of a spell tells people
            set_tenant(db, org_id=sub.org_id)
            notify(
                db,
                "billing",
                [
                    Message(
                        title="A payment failed",
                        text="We could not renew your subscription. Please check your payment "
                        "method; your plan stays while we retry.",
                        link="/settings?tab=plan",
                    )
                ],
            )
            set_tenant(db, bypass=True)
        return "applied", sub.org_id
    if e.kind == "cancelled":
        subscriptions.end(sub)
        return "applied", sub.org_id
    return "ignored", None


def receive(db: Session, cat: Catalogue, event: Event) -> str:
    """Store the event once and apply it. Returns the outcome ("duplicate" for repeats)."""
    set_tenant(db, bypass=True)
    if not event.event_id:
        raise SignatureError("no event id")
    if db.scalar(
        select(PaymentEvent.id).where(
            PaymentEvent.provider == event.provider, PaymentEvent.event_id == event.event_id
        )
    ):
        return "duplicate"
    outcome, org_id = _apply(db, cat, event)
    db.add(
        PaymentEvent(
            provider=event.provider,
            event_id=event.event_id[:200],
            event_type=event.event_type[:100],
            org_id=org_id,
            subscription_ref=(event.subscription_ref or None) and event.subscription_ref[:200],
            outcome=outcome,
            details={"kind": event.kind, "plan": event.plan, "interval": event.interval},
            occurred_at=event.occurred_at,
        )
    )
    db.flush()
    log.info("payment event %s %s: %s", event.provider, event.event_type, outcome)
    return outcome
