"""Subscriptions (B.2), independent of the payment provider.

States: ``trialing`` → ``active`` (paid) → ``ended``; ``past_due`` is set by the payment
providers when a renewal fails (B.16). Rules:

- An upgrade (a bigger plan, or a paid plan after a trial) needs a payment. Until the
  payment providers exist (B.3, B.4, blocked on Q2/Q3) customers are told to contact
  us, and staff record it as a ``manual`` (invoiced) subscription.
- A downgrade needs no payment and takes effect at the end of the paid period.
- Cancelling keeps everything until the end of the paid period; a trial cancelled ends
  at once. Resuming undoes a cancellation that has not taken effect yet.
- An ended subscription falls back to the default (free) plan. Data is never deleted
  because of billing: over a limit, CostLens only refuses to add more (B.1).
"""

from __future__ import annotations

import calendar
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from costlens.billing.plans import Catalogue
from costlens.config import DeploymentMode, Settings
from costlens.core.errors import AppError
from costlens.db.base import utcnow
from costlens.db.tenant import set_tenant
from costlens.models import Organization, Subscription

INTERVALS = ("monthly", "annual")
LIVE = ("trialing", "active", "past_due")


def add_months(when: datetime, months: int) -> datetime:
    month = when.month - 1 + months
    year, month = when.year + month // 12, month % 12 + 1
    day = min(when.day, calendar.monthrange(year, month)[1])
    return when.replace(year=year, month=month, day=day)


def period_end(start: datetime, interval: str) -> datetime:
    return add_months(start, 12 if interval == "annual" else 1)


def get(db: Session, org_id: Any) -> Subscription | None:
    return db.scalar(select(Subscription).where(Subscription.org_id == org_id))


def _aware(value: datetime) -> datetime:
    from datetime import UTC

    return value if value.tzinfo else value.replace(tzinfo=UTC)


def plan_id_of(sub: Subscription, now: datetime | None = None) -> str | None:
    """The plan in force, or None when the subscription no longer gives one."""
    now = now or utcnow()
    if sub.status == "ended":
        return None
    if sub.status == "trialing" and sub.trial_ends_at and _aware(sub.trial_ends_at) <= now:
        return None  # trial over; the hourly job marks it ended
    return sub.plan_id


def start_trial(
    db: Session, org: Organization, settings: Settings, now: datetime | None = None
) -> Subscription | None:
    """New SaaS organisations start on a trial of the trial plan (9.5)."""
    from costlens.billing.plans import catalogue

    if settings.deployment_mode is not DeploymentMode.SAAS or settings.trial_days <= 0:
        return None
    if org.is_demo or org.partner_id is not None or get(db, org.id) is not None:
        return None
    now = now or utcnow()
    ends = now + timedelta(days=settings.trial_days)
    sub = Subscription(
        org_id=org.id,
        plan_id=catalogue(settings).trial_plan,
        status="trialing",
        interval="monthly",
        trial_ends_at=ends,
        current_period_start=now,
        current_period_end=ends,
    )
    db.add(sub)
    db.flush()
    return sub


def _rank(cat: Catalogue, plan_id: str | None) -> int:
    order = list(cat.plans)
    return order.index(plan_id) if plan_id in order else -1


def activate(
    db: Session,
    org: Organization,
    plan_id: str,
    interval: str,
    *,
    provider: str = "manual",
    provider_ref: str | None = None,
    now: datetime | None = None,
) -> Subscription:
    """A paid plan starts now (after a payment, or recorded by staff for an invoice)."""
    now = now or utcnow()
    sub = get(db, org.id)
    if sub is None:
        sub = Subscription(org_id=org.id, plan_id=plan_id)
        db.add(sub)
    sub.plan_id, sub.interval, sub.status = plan_id, interval, "active"
    sub.provider, sub.provider_ref = provider, provider_ref
    sub.current_period_start, sub.current_period_end = now, period_end(now, interval)
    sub.cancel_at_period_end, sub.pending_plan_id, sub.ended_at = False, None, None
    sub.past_due_since, sub.reminders_sent = None, 0
    db.flush()
    return sub


def change(
    db: Session,
    org: Organization,
    cat: Catalogue,
    plan_id: str,
    interval: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """A customer's own change of plan. Downgrades are scheduled; upgrades need a payment."""
    now = now or utcnow()
    if cat.get(plan_id) is None:
        raise AppError(f"Unknown plan {plan_id!r}.", status=422, code="unknown_plan")
    if interval not in INTERVALS:
        raise AppError("Choose monthly or annual.", status=422, code="bad_interval")
    sub = get(db, org.id)
    current = plan_id_of(sub, now) if sub else None
    paying = sub is not None and sub.status in ("active", "past_due") and current is not None
    if paying and sub is not None and _rank(cat, plan_id) < _rank(cat, current):
        sub.pending_plan_id, sub.cancel_at_period_end = plan_id, False
        db.flush()
        return {"status": "scheduled", "effective_at": sub.current_period_end.isoformat()}
    if paying and sub is not None and plan_id == current and interval == sub.interval:
        sub.pending_plan_id = None  # back to the current plan: drop a scheduled downgrade
        db.flush()
        return {"status": "unchanged"}
    if plan_id == cat.default_plan:  # moving to the free plan needs no payment
        if sub is not None and paying:
            sub.pending_plan_id = plan_id
            db.flush()
            return {"status": "scheduled", "effective_at": sub.current_period_end.isoformat()}
        if sub is not None:
            end(sub, now)
        return {"status": "changed"}
    raise AppError(
        "Online payment is not available yet. Contact us to upgrade and we will set it up.",
        status=409,
        code="payment_unavailable",
    )


def cancel(sub: Subscription, now: datetime | None = None) -> dict[str, Any]:
    if sub.status == "ended":
        raise AppError("This subscription has already ended.", status=409, code="ended")
    if sub.status == "trialing":
        end(sub, now)
        return {"status": "ended"}
    sub.cancel_at_period_end = True
    return {"status": "cancelling", "effective_at": sub.current_period_end.isoformat()}


def resume(sub: Subscription) -> None:
    if sub.status == "ended":
        raise AppError("This subscription has already ended.", status=409, code="ended")
    sub.cancel_at_period_end = False


def end(sub: Subscription, now: datetime | None = None) -> None:
    sub.status, sub.ended_at = "ended", now or utcnow()
    sub.cancel_at_period_end, sub.pending_plan_id = False, None


def mark_past_due(sub: Subscription, now: datetime | None = None) -> bool:
    """A renewal payment failed. Returns True when this starts a new past-due spell."""
    if sub.status == "ended":
        return False
    started = sub.status != "past_due" or sub.past_due_since is None
    sub.status = "past_due"
    if started:
        sub.past_due_since, sub.reminders_sent = now or utcnow(), 0
    return started


def mark_paid(sub: Subscription) -> None:
    sub.status, sub.past_due_since, sub.reminders_sent = "active", None, 0


def reminder_days(grace_days: int) -> list[int]:
    """Days after the failed payment on which the owners are emailed."""
    return sorted({d for d in (0, 3, 7, grace_days - 1) if 0 <= d < grace_days})


def _email_owners(db: Session, org_id: Any, subject: str, text: str) -> None:
    from costlens.accounts.roles import Role
    from costlens.core.inbox import queue_email
    from costlens.models import Membership, User

    emails = db.scalars(
        select(User.email)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.org_id == org_id, Membership.role == Role.OWNER)
    ).all()
    for email in emails:
        queue_email(email, subject, text)


def dunning(db: Session, now: datetime, grace_days: int) -> dict[str, int]:
    """Past-due subscriptions: remind the owners, and move to the free plan when the grace
    period is over. Nothing is deleted. The caller sets a bypass tenant."""
    from costlens.core.notify import Message, notify

    counts = {"reminded": 0, "downgraded_unpaid": 0}
    subs = db.scalars(select(Subscription).where(Subscription.status == "past_due")).all()
    for sub in subs:
        since = _aware(sub.past_due_since or now)
        age = (now - since).days
        if age >= grace_days:
            end(sub, now)
            counts["downgraded_unpaid"] += 1
            text = (
                "We could not collect the payment for your CostLens subscription, so your "
                "organisation is now on the free plan. Everything you set up is kept; choose "
                "a plan again in Settings → Plan to get your features back."
            )
            _email_owners(
                db, sub.org_id, "CostLens: your organisation moved to the free plan", text
            )
            set_tenant(db, org_id=sub.org_id)
            notify(
                db,
                "billing",
                [Message(title="Moved to the free plan", text=text, link="/settings?tab=plan")],
            )
            set_tenant(db, bypass=True)
            continue
        due = sum(1 for d in reminder_days(grace_days) if d <= age)
        if due > (sub.reminders_sent or 0):
            left = grace_days - age
            text = (
                "A payment for your CostLens subscription did not go through. Please check "
                "the payment method with your bank or card provider; we will keep trying. "
                f"Your plan stays for {left} more day{'s' if left != 1 else ''}, then the "
                "organisation moves to the free plan (nothing is deleted)."
            )
            _email_owners(db, sub.org_id, "CostLens: payment failed, please check", text)
            sub.reminders_sent = due
            counts["reminded"] += 1
    db.flush()
    return counts


def roll(db: Session, now: datetime | None = None, grace_days: int = 14) -> dict[str, int]:
    """Hourly: apply what is due at the end of a period, and follow up failed payments.
    The caller sets a bypass tenant."""
    from costlens.core.notify import Message, notify

    now = now or utcnow()
    counts = {"ended": 0, "downgraded": 0, "renewed": 0}
    due = db.scalars(
        select(Subscription).where(
            Subscription.status.in_(LIVE), Subscription.current_period_end <= now
        )
    ).all()
    for sub in due:
        message: Message | None = None
        if sub.status == "trialing":
            end(sub, now)
            counts["ended"] += 1
            message = Message(
                title="Your free trial has ended",
                text="You are on the free plan now. Everything you set up is kept.",
                link="/settings?tab=plan",
            )
        elif sub.cancel_at_period_end:
            end(sub, now)
            counts["ended"] += 1
            message = Message(
                title="Your subscription has ended",
                text="You are on the free plan now. Everything you set up is kept.",
                link="/settings?tab=plan",
            )
        elif sub.pending_plan_id:
            sub.plan_id, sub.pending_plan_id = sub.pending_plan_id, None
            sub.current_period_start = sub.current_period_end
            sub.current_period_end = period_end(sub.current_period_start, sub.interval)
            counts["downgraded"] += 1
            message = Message(
                title="Your plan has changed",
                text="The change you scheduled has taken effect.",
                link="/settings?tab=plan",
                severity="info",
            )
        elif sub.provider == "manual":  # invoiced by staff: renews until they end it
            sub.current_period_start = sub.current_period_end
            sub.current_period_end = period_end(sub.current_period_start, sub.interval)
            counts["renewed"] += 1
        # Provider subscriptions renew through the provider's webhooks (B.5, B.16).
        if message is not None:
            set_tenant(db, org_id=sub.org_id)
            notify(db, "billing", [message])
            set_tenant(db, bypass=True)
    db.flush()
    counts.update(dunning(db, now, grace_days))
    return counts
