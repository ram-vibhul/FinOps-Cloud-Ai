"""Email anomaly alerts to an organisation's owners and admins."""

from __future__ import annotations

import html
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from costlens.accounts.roles import Role
from costlens.core.email import Email
from costlens.costs.anomalies import alert_settings
from costlens.db.base import utcnow
from costlens.db.tenant import set_tenant
from costlens.models import CostAnomaly, Membership, Organization, User


def _money(value: float, currency: str) -> str:
    return f"{value:,.2f} {currency}"


def pending_alert(db: Session, org_id: uuid.UUID, base_url: str) -> list[Email]:
    """Emails for anomalies not yet notified; marks them notified. Empty if disabled."""
    set_tenant(db, org_id=org_id)
    org = db.get(Organization, org_id)
    if org is None or org.is_demo or not alert_settings(org).anomaly_alerts:
        return []
    anomalies = db.scalars(
        select(CostAnomaly)
        .where(CostAnomaly.notified_at.is_(None), CostAnomaly.status == "open")
        .order_by(CostAnomaly.impact.desc())
    ).all()
    if not anomalies:
        return []
    recipients = db.scalars(
        select(User.email)
        .join(Membership, Membership.user_id == User.id)
        .where(
            Membership.org_id == org_id,
            Membership.role.in_((Role.OWNER, Role.ADMIN)),
            User.is_active.is_(True),
        )
    ).all()
    from costlens.costs.explain import explain

    why = {a.id: explain(db, a)["text"] for a in anomalies[:5]}
    lines, rows = [], []
    for a in anomalies[:20]:
        what = a.key if a.dimension != "total" else "All spend"
        lines.append(
            f"- {a.day:%d %b}: {what} ({a.dimension}) spent "
            f"{_money(a.actual, a.currency)}, about {_money(a.expected, a.currency)} "
            f"expected (+{_money(a.impact, a.currency)})"
            + (f"\n  {why[a.id]}" if a.id in why else "")
        )
        rows.append(
            f"<tr><td>{a.day:%d %b}</td><td>{html.escape(what)}</td>"
            f"<td>{html.escape(a.dimension)}</td>"
            f"<td style='text-align:right'>{_money(a.actual, a.currency)}</td>"
            f"<td style='text-align:right'>{_money(a.expected, a.currency)}</td>"
            f"<td style='text-align:right'>+{_money(a.impact, a.currency)}</td></tr>"
            + (
                "<tr><td></td><td colspan='5' style='color:#555'>"
                f"{html.escape(why[a.id])}</td></tr>"
                if a.id in why
                else ""
            )
        )
    url = f"{base_url.rstrip('/')}/costs"
    more = f"\n…and {len(anomalies) - 20} more." if len(anomalies) > 20 else ""
    subject = (
        f"{org.name}: {len(anomalies)} unusual cost increase{'s' if len(anomalies) != 1 else ''}"
    )
    text = (
        f"CostLens found spend that is higher than usual in {org.name}:\n\n"
        + "\n".join(lines)
        + more
        + f"\n\nReview: {url}\n"
    )
    body = (
        f"<p>CostLens found spend that is higher than usual in "
        f"<strong>{html.escape(org.name)}</strong>:</p><table cellpadding='4'>"
        "<tr><th>Day</th><th>What</th><th>By</th><th>Spent</th><th>Expected</th>"
        "<th>Extra</th></tr>" + "".join(rows) + f"</table><p><a href='{html.escape(url)}'>"
        "Review in CostLens</a></p>"
    )
    now = utcnow()
    for a in anomalies:
        a.notified_at = now
    db.flush()
    from costlens.core.notify import Message, notify

    notify(
        db,
        "cost_anomaly",
        [
            Message(
                title=subject,
                text="\n".join(line.replace("\n  ", " — ") for line in lines[:10])
                + (f"\n…and {len(anomalies) - 10} more." if len(anomalies) > 10 else ""),
                link=url,
            )
        ],
    )
    return [
        Email(to=r, subject=subject, text=text, html=body, tags={"kind": "anomaly"})
        for r in recipients
    ]
