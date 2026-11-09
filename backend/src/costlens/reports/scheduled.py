"""Scheduled reports: build a report as PDF (reportlab) or CSV and email it.

Kinds: cost summary (last month, or month to date for daily/weekly schedules), savings
(totals and the quick-wins plan), budgets, showback for a dimension, and the one-page
leadership report for the last full month (see ``reports.leadership``)."""

from __future__ import annotations

import base64
import csv
import io
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from costlens.core.email import Email
from costlens.db.base import utcnow
from costlens.models import Organization, ReportSchedule

KINDS = {
    "cost_summary": "Cost summary",
    "savings": "Savings",
    "budgets": "Budgets",
    "showback": "Showback",
    "leadership": "Leadership one-pager",
}
SEND_HOUR = 7


@dataclass
class Section:
    heading: str
    header: list[str]
    rows: list[list[Any]]


def _money(v: float, c: str) -> str:
    return f"{v:,.2f} {c}"


def _period(frequency: str, today: date) -> tuple[date, str]:
    if frequency == "monthly":
        last = today.replace(day=1) - timedelta(days=1)
        return last.replace(day=1), last.strftime("%B %Y")
    return today.replace(day=1), f"{today:%B %Y} so far"


def sections_for(
    db: Session, org_id: uuid.UUID, kind: str, frequency: str, params: dict[str, Any]
) -> tuple[str, list[Section]]:
    today = utcnow().date()
    org = db.get(Organization, org_id)
    currency = org.default_currency if org else "USD"
    if kind == "cost_summary":
        from costlens.costs.statements import build

        month, label = _period(frequency, today)
        s = build(db, org_id, month)
        return f"Cost summary — {label} — total {_money(s['total'], s['currency'])}", [
            Section(
                "By service",
                ["Service", "Cost"],
                [[r["key"], _money(r["cost"], s["currency"])] for r in s["by_service"]],
            ),
            Section(
                "By account",
                ["Account", "Cost"],
                [[r["key"], _money(r["cost"], s["currency"])] for r in s["by_account"]],
            ),
            Section(
                "By charge type",
                ["Type", "Cost"],
                [[r["key"], _money(r["cost"], s["currency"])] for r in s["by_category"]],
            ),
        ]
    if kind == "savings":
        from costlens.findings.plan import build as plan

        p = plan(db)
        total = sum(b["savings_monthly"] for b in p["buckets"])
        cur = p["currency"] or currency
        return f"Savings — {_money(total, cur)} a month open", [
            Section(
                f"{b['label']} ({_money(b['savings_monthly'], cur)} a month)",
                ["Saving", "Per month", "Risk", "Effort"],
                [
                    [i["title"], _money(i["savings_monthly"], cur), i["risk"], i["effort"]]
                    for i in b["items"][:15]
                ],
            )
            for b in p["buckets"]
        ]
    if kind == "budgets":
        from costlens.costs.budgets import all_status

        rows = all_status(db, org_id, today)
        return f"Budgets — {len(rows)} budget(s)", [
            Section(
                "Budgets",
                ["Budget", "Amount", "Spent", "Forecast", "Forecast %"],
                [
                    [
                        b["name"],
                        _money(b["amount"], b["currency"]),
                        _money(b["spent"], b["currency"]),
                        _money(b["forecast"], b["currency"]),
                        f"{b['forecast_pct'] or 0:.0f}%",
                    ]
                    for b in rows
                ],
            )
        ]
    if kind == "showback":
        from costlens.costs.allocation import showback

        dimension = str(params.get("dimension") or "tag:team")
        month, label = _period("monthly", today)
        s = showback(db, org_id, month, dimension)
        return f"Showback by {dimension} — {label}", [
            Section(
                dimension,
                [dimension, "Own cost", "Shared", "Total"],
                [
                    [
                        r["value"],
                        _money(r["direct"], s["currency"]),
                        _money(r["shared_total"], s["currency"]),
                        _money(r["total"], s["currency"]),
                    ]
                    for r in s["rows"]
                ],
            )
        ]
    if kind == "leadership":
        from costlens.reports.leadership import build

        d = build(db, org_id)
        c = d["currency"]
        return f"Leadership report — {d['month_label']} — total {_money(d['total'], c)}", [
            Section("Summary", ["Point"], [[x] for x in d["sentences"]]),
            Section(
                "Biggest changes by service",
                ["Service", "Change"],
                [[m["key"], _money(m["change"], c)] for m in d["grew"] + d["shrank"]],
            ),
            Section(
                "Monthly budgets",
                ["Budget", "Amount", "Spent", "Spent %"],
                [
                    [
                        b["name"],
                        _money(b["amount"], b["currency"]),
                        _money(b["spent"], b["currency"]),
                        f"{b['pct'] or 0:.0f}%",
                    ]
                    for b in d["budgets"]
                ],
            ),
        ]
    raise ValueError(f"unknown report kind {kind}")


def to_pdf(title: str, org_name: str, sections: list[Section]) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=title,
    )
    styles = getSampleStyleSheet()
    story: list[Any] = [
        Paragraph(title, styles["Title"]),
        Paragraph(org_name, styles["Normal"]),
        Spacer(1, 5 * mm),
    ]
    for s in sections:
        story.append(Paragraph(s.heading, styles["Heading2"]))
        if not s.rows:
            story.append(Paragraph("Nothing to show.", styles["Normal"]))
            continue
        data: list[list[Any]] = [s.header] + [
            [Paragraph(str(c), styles["Normal"]) if i == 0 else str(c) for i, c in enumerate(r)]
            for r in s.rows
        ]
        t = Table(data, repeatRows=1)
        t.setStyle(
            TableStyle(
                [
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ]
            )
        )
        story += [t, Spacer(1, 4 * mm)]
    doc.build(story)
    return buf.getvalue()


def to_csv(sections: list[Section]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    for s in sections:
        w.writerow([s.heading])
        w.writerow(s.header)
        w.writerows(s.rows)
        w.writerow([])
    return buf.getvalue().encode()


def render(
    db: Session, org_id: uuid.UUID, kind: str, frequency: str, params: dict[str, Any], fmt: str
) -> tuple[str, str, bytes, str]:
    org = db.get(Organization, org_id)
    title, sections = sections_for(db, org_id, kind, frequency, params)
    stamp = utcnow().date().isoformat()
    if fmt == "csv":
        return f"costlens-{kind}-{stamp}.csv", "text/csv", to_csv(sections), title
    if kind == "leadership":
        from costlens.reports import leadership

        d = leadership.build(db, org_id)
        return (
            f"costlens-leadership-{d['month']}.pdf",
            "application/pdf",
            leadership.to_pdf(d),
            title,
        )
    return (
        f"costlens-{kind}-{stamp}.pdf",
        "application/pdf",
        to_pdf(title, org.name if org else "", sections),
        title,
    )


def is_due(s: ReportSchedule, now: datetime) -> bool:
    if not s.enabled or not s.recipients or now.hour < SEND_HOUR:
        return False
    last = s.last_sent_at
    if last is not None and last.date() == now.date():
        return False
    if s.frequency == "weekly":
        return now.weekday() == s.weekday
    if s.frequency == "monthly":
        return now.day == s.day
    return True


def send(db: Session, org_id: uuid.UUID, s: ReportSchedule, deliver: Callable[[dict], None]) -> int:
    filename, content_type, data, title = render(
        db, org_id, s.kind, s.frequency, s.params or {}, s.format
    )
    attachment = {
        "filename": filename,
        "content_type": content_type,
        "data": base64.b64encode(data).decode(),
    }
    text = f"{title}\n\nThe full report is attached ({filename})."
    if s.kind == "leadership":
        from costlens.reports import leadership

        text = leadership.to_text(leadership.build(db, org_id))
    for to in s.recipients:
        deliver(
            Email(
                to=to,
                subject=f"CostLens: {s.name}",
                text=text,
                attachments=[attachment],
            ).to_dict()
        )
    s.last_sent_at, s.last_error = utcnow(), None
    db.flush()
    return len(s.recipients)


def send_due(
    db: Session, org_id: uuid.UUID, deliver: Callable[[dict], None], now: datetime | None = None
) -> int:
    now = now or utcnow()
    sent = 0
    for s in db.scalars(select(ReportSchedule)):
        if not is_due(s, now):
            continue
        try:
            sent += send(db, org_id, s, deliver)
        except Exception as error:
            s.last_error = f"{type(error).__name__}: {error}"[:500]
            db.flush()
    return sent
