"""Failed payments (B.16): reminders to owners, a grace period, then the free plan."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from costlens.accounts.roles import Role
from costlens.billing import subscriptions
from costlens.core.email import MemorySender, configure_email
from costlens.db import Database
from costlens.db.tenant import set_tenant
from costlens.jobs import load_all_jobs
from costlens.models import Organization, Subscription
from tests.helpers import add_to_org, make_org

START = datetime(2026, 11, 1, 10, tzinfo=UTC)


def setup(db: Database):
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    add_to_org(db, org_id, "admin@acme.com", Role.ADMIN)  # admins get no billing emails
    with db.session() as s:
        set_tenant(s, bypass=True)
        org = s.get(Organization, org_id)
        assert org is not None
        sub = subscriptions.activate(
            s, org, "team", "monthly", provider="stripe", provider_ref="sub_1", now=START
        )
        assert subscriptions.mark_past_due(sub, START) is True
        assert subscriptions.mark_past_due(sub, START + timedelta(days=1)) is False  # same spell
    return org_id


def roll_at(db: Database, days: float) -> dict[str, int]:
    with db.session() as s:
        set_tenant(s, bypass=True)
        return subscriptions.dunning(s, START + timedelta(days=days), grace_days=14)


def state(db: Database, org_id) -> Subscription:
    with db.session() as s:
        set_tenant(s, bypass=True)
        sub = s.scalar(select(Subscription).where(Subscription.org_id == org_id))
        assert sub is not None
        s.expunge(sub)
        return sub


def test_reminder_days() -> None:
    assert subscriptions.reminder_days(14) == [0, 3, 7, 13]
    assert subscriptions.reminder_days(3) == [0, 2]


def test_reminders_then_free_plan(client: TestClient, db: Database) -> None:
    load_all_jobs()
    outbox = MemorySender()
    configure_email(outbox)
    org_id = setup(db)
    assert roll_at(db, 0.1)["reminded"] == 1
    assert roll_at(db, 1)["reminded"] == 0  # no repeat on the same step
    assert roll_at(db, 3)["reminded"] == 1
    assert roll_at(db, 7.5)["reminded"] == 1
    assert roll_at(db, 13)["reminded"] == 1
    assert state(db, org_id).status == "past_due"
    assert roll_at(db, 14)["downgraded_unpaid"] == 1
    sub = state(db, org_id)
    assert sub.status == "ended"
    to = {m.to for m in outbox.outbox}
    assert to == {"o@acme.com"}  # owners only
    subjects = [m.subject for m in outbox.outbox]
    assert subjects.count("CostLens: payment failed, please check") == 4
    assert subjects[-1] == "CostLens: your organisation moved to the free plan"
    assert "6 more days" in outbox.outbox[2].text or "7 more days" in outbox.outbox[2].text


def test_payment_recovers(db: Database) -> None:
    org_id = setup(db)
    roll_at(db, 3)
    with db.session() as s:
        set_tenant(s, bypass=True)
        sub = s.scalar(select(Subscription).where(Subscription.org_id == org_id))
        assert sub is not None
        subscriptions.mark_paid(sub)
    sub = state(db, org_id)
    assert (sub.status, sub.past_due_since, sub.reminders_sent) == ("active", None, 0)
    assert roll_at(db, 20) == {"reminded": 0, "downgraded_unpaid": 0}
