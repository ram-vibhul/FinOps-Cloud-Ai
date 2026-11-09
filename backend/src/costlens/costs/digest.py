"""Weekly digest: last week's spend in a few lines, sent on Monday morning.

Spend for the last 7 days with data against the 7 before, the services that moved most,
unusual increases found that week, budgets heading over, and the savings waiting. It goes
to channels subscribed to ``weekly_digest``, and by email to owners and admins when the
organisation turns that on. Sent once a week."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from costlens.accounts.roles import Role
from costlens.core.email import Email
from costlens.core.notify import Message, notify
from costlens.costs import analytics
from costlens.costs.anomalies import alert_settings
from costlens.costs.budgets import all_status, daily_spend
from costlens.db.base import utcnow
from costlens.models import CostAnomaly, Finding, Membership, Organization, User


def _m(v: float, c: str) -> str:
    return f"{v:,.2f} {c}"


def build(db: Session, org_id: uuid.UUID, today: date | None = None) -> dict[str, Any] | None:
    org = db.get(Organization, org_id)
    if org is None:
        return None
    today = today or utcnow().date()
    end = analytics.latest_date(db, org_id)
    if end is None:
        return None
    cur = org.default_currency
    start, prev_start = end - timedelta(days=6), end - timedelta(days=13)
    days = daily_spend(db, org_id, {}, prev_start, end, "effective", cur)
    week = sum(v for d, v in days.items() if d >= start)
    before = sum(v for d, v in days.items() if d < start)
    movers = analytics.movers(db, org_id, cur, "effective", "service", end, 7, 5)["items"]
    anomalies = list(
        db.scalars(
            select(CostAnomaly)
            .where(CostAnomaly.day >= start, CostAnomaly.day <= end)
            .order_by(CostAnomaly.impact.desc())
        )
    )
    budgets = [b for b in all_status(db, org_id, today) if (b["forecast_pct"] or 0) >= 100]
    from costlens.findings.service import _per_resource

    open_findings = list(
        db.scalars(select(Finding).where(Finding.status.in_(("open", "approved"))))
    )
    return {
        "currency": cur,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "week": round(week, 2),
        "previous": round(before, 2),
        "change_pct": round(100 * (week - before) / before, 1) if before else None,
        "movers": [
            {"key": m.get("label") or m["key"], "change": m["change"]}
            for m in movers
            if m["change"] != 0
        ][:3],
        "anomalies": [
            {"key": a.key if a.dimension != "total" else "All spend", "impact": round(a.impact, 2)}
            for a in anomalies[:3]
        ],
        "anomaly_count": len(anomalies),
        "budgets_at_risk": [
            {"name": b["name"], "forecast_pct": b["forecast_pct"]} for b in budgets[:3]
        ],
        "savings_open_monthly": round(sum(_per_resource(open_findings).values()), 2),
        "savings_open_count": len(open_findings),
    }


def lines(d: dict[str, Any]) -> list[str]:
    c = d["currency"]
    change = (
        ""
        if d["change_pct"] is None
        else f", {'up' if d['change_pct'] > 0 else 'down'} "
        f"{abs(d['change_pct']):.0f}% on the week before"
        if d["change_pct"]
        else ", the same as the week before"
    )
    out = [f"Spend {d['start']} to {d['end']}: {_m(d['week'], c)}{change}."]
    if d["movers"]:
        out.append(
            "Moved most: "
            + ", ".join(
                f"{m['key']} ({'+' if m['change'] > 0 else ''}{_m(m['change'], c)})"
                for m in d["movers"]
            )
            + "."
        )
    if d["anomaly_count"]:
        top = ", ".join(f"{a['key']} (+{_m(a['impact'], c)})" for a in d["anomalies"])
        out.append(f"Unusual increases: {d['anomaly_count']} ({top}).")
    if d["budgets_at_risk"]:
        out.append(
            "Budgets heading over: "
            + ", ".join(f"{b['name']} ({b['forecast_pct']:.0f}%)" for b in d["budgets_at_risk"])
            + "."
        )
    if d["savings_open_count"]:
        out.append(
            f"Savings waiting: {_m(d['savings_open_monthly'], c)} a month across "
            f"{d['savings_open_count']} item(s)."
        )
    return out


def send(
    db: Session, org_id: uuid.UUID, base_url: str, today: date | None = None, enqueue: Any = None
) -> int:
    """Send this week's digest once (keyed by ISO week). Returns messages sent."""
    org = db.get(Organization, org_id)
    if org is None or org.is_demo:
        return 0
    today = today or utcnow().date()
    year, week_no, _ = today.isocalendar()
    stamp = f"{year}-W{week_no:02d}"
    state = dict((org.settings or {}).get("digest_state") or {})
    if state.get("sent") == stamp:
        return 0
    d = build(db, org_id, today)
    if d is None:
        return 0
    body = lines(d)
    url = f"{base_url.rstrip('/')}/costs"
    title = f"{org.name}: weekly cost digest"
    sent = notify(
        db, "weekly_digest", [Message(title=title, text="\n".join(body), link=url, severity="info")]
    )
    if alert_settings(org).weekly_digest_email and enqueue is not None:
        recipients = db.scalars(
            select(User.email)
            .join(Membership, Membership.user_id == User.id)
            .where(
                Membership.org_id == org_id,
                Membership.role.in_((Role.OWNER, Role.ADMIN)),
                User.is_active.is_(True),
            )
        ).all()
        for to in recipients:
            enqueue(
                "email.send",
                message=Email(
                    to=to,
                    subject=title,
                    text="\n".join(body) + f"\n\nOpen CostLens: {url}\n",
                    tags={"kind": "digest"},
                ).to_dict(),
            )
            sent += 1
    org.settings = {**(org.settings or {}), "digest_state": {"sent": stamp}}
    db.flush()
    return sent
