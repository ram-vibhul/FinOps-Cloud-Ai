from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select

from costlens.billing import subscriptions, webhooks
from costlens.db import Database
from costlens.db.tenant import set_tenant
from costlens.models import Organization, PaymentEvent, Subscription
from tests.helpers import make_org

RZP, STRIPE = "rzp-test-secret", "whsec_test"
PATH = "/api/v1/billing/webhooks"


@pytest.fixture
def env(client: TestClient, db: Database):
    s = client.app.state.settings  # type: ignore[attr-defined]
    s.razorpay_webhook_secret = SecretStr(RZP)
    s.stripe_webhook_secret = SecretStr(STRIPE)
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    return {"c": client, "db": db, "org": org_id}


def razorpay(c: TestClient, event: str, sub: dict, event_id: str, secret: str = RZP):
    body = json.dumps(
        {
            "event": event,
            "created_at": int(time.time()),
            "payload": {"subscription": {"entity": sub}},
        }
    ).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return c.post(
        f"{PATH}/razorpay",
        content=body,
        headers={"X-Razorpay-Signature": sig, "X-Razorpay-Event-Id": event_id},
    )


def stripe(c: TestClient, payload: dict, stamp: int | None = None, secret: str = STRIPE):
    body = json.dumps(payload).encode()
    t = stamp or int(time.time())
    sig = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return c.post(f"{PATH}/stripe", content=body, headers={"Stripe-Signature": f"t={t},v1={sig}"})


def subscription(db: Database, org_id) -> Subscription:
    with db.session() as s:
        set_tenant(s, bypass=True)
        sub = s.scalar(select(Subscription).where(Subscription.org_id == org_id))
        assert sub is not None
        s.expunge(sub)
        return sub


def test_razorpay_lifecycle(env) -> None:
    c, db, org = env["c"], env["db"], env["org"]
    end = int((datetime.now(UTC) + timedelta(days=30)).timestamp())
    entity = {
        "id": "sub_123",
        "current_end": end,
        "notes": {"org_id": str(org), "plan": "team", "interval": "monthly"},
    }
    assert razorpay(c, "subscription.activated", entity, "evt_1").json() == {"status": "applied"}
    sub = subscription(db, org)
    assert (sub.plan_id, sub.status, sub.provider, sub.provider_ref) == (
        "team",
        "active",
        "razorpay",
        "sub_123",
    )
    # The same delivery again changes nothing.
    assert razorpay(c, "subscription.activated", entity, "evt_1").json() == {"status": "duplicate"}
    later = {**entity, "current_end": end + 30 * 86400}
    assert razorpay(c, "subscription.charged", later, "evt_2").json()["status"] == "applied"
    assert int(subscriptions._aware(subscription(db, org).current_period_end).timestamp()) == (
        end + 30 * 86400
    )
    razorpay(c, "subscription.halted", later, "evt_3")
    assert subscription(db, org).status == "past_due"
    razorpay(c, "subscription.cancelled", later, "evt_4")
    assert subscription(db, org).status == "ended"
    unknown = razorpay(c, "subscription.charged", {"id": "sub_zzz"}, "evt_5").json()
    assert unknown == {"status": "unknown_subscription"}
    assert razorpay(c, "payment.captured", {}, "evt_6").json() == {"status": "ignored"}
    with db.session() as s:
        set_tenant(s, bypass=True)
        rows = s.scalars(select(PaymentEvent).order_by(PaymentEvent.created_at)).all()
        assert [r.event_id for r in rows] == ["evt_1", "evt_2", "evt_3", "evt_4", "evt_5", "evt_6"]


def test_razorpay_signature_is_required(env) -> None:
    c = env["c"]
    bad = razorpay(c, "subscription.activated", {"id": "x"}, "evt_9", secret="wrong")
    assert bad.status_code == 400 and bad.json()["code"] == "bad_signature"
    no_sig = c.post(f"{PATH}/razorpay", content=b"{}")
    assert no_sig.status_code == 400


def test_stripe_lifecycle_and_replay(env) -> None:
    c, db, org = env["c"], env["db"], env["org"]
    done = {
        "id": "evt_a",
        "type": "checkout.session.completed",
        "created": int(time.time()),
        "data": {
            "object": {
                "subscription": "sub_S1",
                "metadata": {"org_id": str(org), "plan": "business", "interval": "annual"},
            }
        },
    }
    assert stripe(c, done).json() == {"status": "applied"}
    sub = subscription(db, org)
    assert (sub.plan_id, sub.interval, sub.provider) == ("business", "annual", "stripe")
    failed = {
        "id": "evt_b",
        "type": "invoice.payment_failed",
        "data": {"object": {"subscription": "sub_S1"}},
    }
    stripe(c, failed)
    assert subscription(db, org).status == "past_due"
    end = int((datetime.now(UTC) + timedelta(days=400)).timestamp())
    paid = {
        "id": "evt_c",
        "type": "invoice.paid",
        "data": {
            "object": {"subscription": "sub_S1", "lines": {"data": [{"period": {"end": end}}]}}
        },
    }
    stripe(c, paid)
    sub = subscription(db, org)
    assert (
        sub.status == "active"
        and int(subscriptions._aware(sub.current_period_end).timestamp()) == end
    )
    deleted = {
        "id": "evt_d",
        "type": "customer.subscription.deleted",
        "data": {"object": {"id": "sub_S1"}},
    }
    stripe(c, deleted)
    assert subscription(db, org).status == "ended"
    # Old signatures (replays) and wrong secrets are refused.
    old = stripe(c, {**paid, "id": "evt_e"}, stamp=int(time.time()) - 600)
    assert old.status_code == 400
    assert stripe(c, {**paid, "id": "evt_f"}, secret="nope").status_code == 400


def test_checkout_without_details_is_recorded_not_applied(env) -> None:
    c, db = env["c"], env["db"]
    out = stripe(
        c,
        {
            "id": "evt_x",
            "type": "checkout.session.completed",
            "data": {
                "object": {"subscription": "sub_Q", "metadata": {"org_id": str(uuid.uuid4())}}
            },
        },
    ).json()
    assert out == {"status": "missing_checkout_details"}
    with db.session() as s:
        set_tenant(s, bypass=True)
        assert s.scalars(select(Subscription)).all() == []
        assert s.scalar(select(Organization.id)) is not None


def test_unconfigured_provider_is_hidden(client: TestClient) -> None:
    assert client.post(f"{PATH}/stripe", content=b"{}").status_code == 404
    assert client.post(f"{PATH}/paypal", content=b"{}").status_code == 404


def test_stripe_header_parsing() -> None:
    with pytest.raises(webhooks.SignatureError):
        webhooks.verify_stripe(STRIPE, b"{}", "v1=abc")
    with pytest.raises(webhooks.SignatureError):
        webhooks.verify_stripe(STRIPE, b"{}", None)
