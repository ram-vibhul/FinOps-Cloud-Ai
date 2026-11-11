"""One-page monthly leadership report: last month's bill in a few numbers and plain
sentences, as a single-page PDF (and the same text in the email body).

Covers the last full month: total and change against the month before, what grew and
shrank most, budgets that went over, savings found and kept, commitment coverage, the
first unit cost, and how many anomalies and open policy violations there were."""

from __future__ import annotations

import io
import uuid
from calendar import monthrange
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ccradar.db.base import utcnow
from ccradar.models import CostAnomaly, Finding, Organization, PolicyViolation, UnitMetric

MOVERS = 3


def last_full_month(today: date) -> date:
    return (today.replace(day=1) - timedelta(days=1)).replace(day=1)


def _m(v: float | None, c: str) -> str:
    return "—" if v is None else f"{v:,.0f} {c}"


def build(db: Session, org_id: uuid.UUID, month: date | None = None) -> dict[str, Any]:
    from ccradar.costs.budgets import all_status
    from ccradar.costs.statements import statement
    from ccradar.findings.kept import summary as kept_summary
    from ccradar.findings.service import _per_resource

    org = db.get(Organization, org_id)
    month = (month or last_full_month(utcnow().date())).replace(day=1)
    month_end = month.replace(day=monthrange(month.year, month.month)[1])
    prev = last_full_month(month)
    this, before = statement(db, org_id, month), statement(db, org_id, prev)
    cur = this["currency"]
    total, prev_total = float(this["total"]), float(before["total"])
    change_pct = round(100 * (total - prev_total) / prev_total, 1) if prev_total else None

    a = {r["key"]: float(r["cost"]) for r in this["by_service"]}
    b = {r["key"]: float(r["cost"]) for r in before["by_service"]}
    deltas = sorted(
        ((k, a.get(k, 0.0) - b.get(k, 0.0)) for k in set(a) | set(b)), key=lambda kv: kv[1]
    )
    grew = [{"key": k, "change": round(v, 2)} for k, v in reversed(deltas[-MOVERS:]) if v > 0]
    shrank = [{"key": k, "change": round(v, 2)} for k, v in deltas[:MOVERS] if v < 0]

    budgets = [
        {
            "name": s["name"],
            "amount": s["amount"],
            "spent": s["spent"],
            "currency": s["currency"],
            "pct": s["spent_pct"],
        }
        for s in all_status(db, org_id, month_end)
        if s["period"] == "monthly"
    ]
    over = [x for x in budgets if (x["pct"] or 0) >= 100]

    open_findings = list(
        db.scalars(select(Finding).where(Finding.status.in_(("open", "approved"))))
    )
    kept = kept_summary(db)
    kept_month = next(
        (k["kept"] for k in kept["months"] if k["month"] == month.strftime("%Y-%m")), 0.0
    )

    from ccradar.findings.commitments import latest

    snaps = latest(db)
    sp = (snaps[0][0].data.get("savings_plans") or {}) if snaps else {}

    unit = None
    first = db.scalar(select(UnitMetric).order_by(UnitMetric.created_at))
    if first is not None:
        from ccradar.costs.unit_economics import report

        r = report(db, org_id, first, month, month_end)
        unit = {"name": r["metric"]["name"], "unit": r["metric"]["unit"], "per_unit": r["per_unit"]}

    anomalies = (
        db.scalar(
            select(func.count())
            .select_from(CostAnomaly)
            .where(CostAnomaly.last_day >= month, CostAnomaly.last_day <= month_end)
        )
        or 0
    )
    violations = (
        db.scalar(
            select(func.count())
            .select_from(PolicyViolation)
            .where(PolicyViolation.status == "open")
        )
        or 0
    )
    data = {
        "org": org.name if org else "",
        "month": month.strftime("%Y-%m"),
        "month_label": month.strftime("%B %Y"),
        "currency": cur,
        "closed": bool(this.get("closed")),
        "total": round(total, 2),
        "previous_total": round(prev_total, 2),
        "change_pct": change_pct,
        "grew": grew,
        "shrank": shrank,
        "budgets": budgets,
        "budgets_over": over,
        "savings_open_monthly": round(sum(_per_resource(open_findings).values()), 2),
        "savings_open_count": len(open_findings),
        "savings_kept_monthly": round(float(kept_month), 2),
        "commitment_coverage_pct": sp.get("coverage_pct"),
        "commitment_utilization_pct": sp.get("utilization_pct"),
        "unit_cost": unit,
        "anomalies": anomalies,
        "policy_violations": violations,
    }
    data["sentences"] = sentences(data)
    return data


def sentences(d: dict[str, Any]) -> list[str]:
    c = d["currency"]
    out = []
    if d["change_pct"] is None:
        out.append(f"Cloud spend in {d['month_label']} was {_m(d['total'], c)}.")
    else:
        word = "up" if d["change_pct"] > 0 else "down" if d["change_pct"] < 0 else "flat"
        pct = f" {abs(d['change_pct']):.0f}%" if word != "flat" else ""
        out.append(
            f"Cloud spend in {d['month_label']} was {_m(d['total'], c)}, {word}{pct} from "
            f"{_m(d['previous_total'], c)} the month before."
        )
    if d["grew"]:
        out.append(
            "Grew most: "
            + ", ".join(f"{g['key']} (+{_m(g['change'], c)})" for g in d["grew"])
            + "."
        )
    if d["budgets"]:
        if d["budgets_over"]:
            names = ", ".join(f"{b['name']} ({b['pct']:.0f}%)" for b in d["budgets_over"][:3])
            out.append(
                f"{len(d['budgets_over'])} of {len(d['budgets'])} monthly budgets went over: {names}."
            )
        else:
            out.append(f"All {len(d['budgets'])} monthly budgets stayed within their amount.")
    if d["savings_open_count"]:
        out.append(
            f"{_m(d['savings_open_monthly'], c)} a month of savings is open across "
            f"{d['savings_open_count']} item(s); {_m(d['savings_kept_monthly'], c)} a month "
            "was confirmed on this month's bill."
        )
    elif d["savings_kept_monthly"]:
        out.append(
            f"{_m(d['savings_kept_monthly'], c)} a month of savings was confirmed on the bill."
        )
    return out


def to_text(d: dict[str, Any]) -> str:
    c = d["currency"]
    lines = [f"CloudCostRadar leadership report — {d['month_label']}", "", *d["sentences"], ""]
    lines.append(f"Total: {_m(d['total'], c)} (previous month {_m(d['previous_total'], c)})")
    if d["commitment_coverage_pct"] is not None:
        lines.append(f"Savings Plans coverage: {d['commitment_coverage_pct']:.0f}%")
    if d["unit_cost"] and d["unit_cost"]["per_unit"] is not None:
        u = d["unit_cost"]
        lines.append(f"{u['name']}: {u['per_unit']:.4f} {c} per {u['unit']}")
    lines.append(f"Anomalies: {d['anomalies']}; open policy violations: {d['policy_violations']}")
    lines += ["", "The one-page PDF is attached."]
    return "\n".join(lines)


def to_pdf(d: dict[str, Any]) -> bytes:
    """A single A4 page. Content is shrunk to fit rather than spilling to a second page."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        KeepInFrame,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    c = d["currency"]
    buf = io.BytesIO()
    margin = 16 * mm
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=margin,
        bottomMargin=margin,
        title=f"Leadership report {d['month_label']}",
    )
    st = getSampleStyleSheet()
    small = st["Normal"].clone("small", fontSize=8, leading=10, textColor=colors.grey)
    width = A4[0] - 2 * margin

    change = "—" if d["change_pct"] is None else f"{d['change_pct']:+.1f}%"
    cov = d["commitment_coverage_pct"]
    tiles = [
        [
            ("Spend", _m(d["total"], c), f"{change} vs previous month"),
            (
                "Savings open",
                f"{_m(d['savings_open_monthly'], c)}/mo",
                f"{d['savings_open_count']} item(s)",
            ),
            ("Savings kept", f"{_m(d['savings_kept_monthly'], c)}/mo", "confirmed on the bill"),
            ("Commitment coverage", "—" if cov is None else f"{cov:.0f}%", "Savings Plans"),
        ]
    ]
    tile_cells = [
        [
            [
                Paragraph(label, small),
                Paragraph(f"<b>{value}</b>", st["Heading3"]),
                Paragraph(note, small),
            ]
            for label, value, note in row
        ]
        for row in tiles
    ]
    tile_table = Table(tile_cells, colWidths=[width / 4] * 4)
    tile_table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.5, colors.lightgrey),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )

    def small_table(header: list[str], rows: list[list[str]]) -> Any:
        if not rows:
            return Paragraph("Nothing to show.", small)
        t = Table([header, *rows], colWidths=[width * 0.3, width * 0.17])
        t.setStyle(
            TableStyle(
                [
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                    ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ]
            )
        )
        return t

    movers = [[m["key"][:40], f"+{_m(m['change'], c)}"] for m in d["grew"]] + [
        [m["key"][:40], f"-{_m(-m['change'], c)}"] for m in d["shrank"]
    ]
    budgets = [
        [b["name"][:40], f"{b['pct'] or 0:.0f}%"]
        for b in sorted(d["budgets"], key=lambda x: -(x["pct"] or 0))[:6]
    ]
    pair = Table(
        [
            [
                Paragraph("Biggest changes by service", st["Heading4"]),
                Paragraph("Monthly budgets (spent)", st["Heading4"]),
            ],
            [small_table(["Service", "Change"], movers), small_table(["Budget", "Spent"], budgets)],
        ],
        colWidths=[width / 2, width / 2],
    )
    pair.setStyle(
        TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)])
    )

    footer = [
        f"Anomalies in the month: {d['anomalies']}",
        f"Open policy violations: {d['policy_violations']}",
    ]
    if d["unit_cost"] and d["unit_cost"]["per_unit"] is not None:
        u = d["unit_cost"]
        footer.insert(0, f"{u['name']}: {u['per_unit']:.4f} {c} per {u['unit']}")
    status = "closed month" if d["closed"] else "month not closed yet; figures may still change"

    story: list[Any] = [
        Paragraph(f"Cloud costs — {d['month_label']}", st["Title"]),
        Paragraph(f"{d['org']} · {status}", small),
        Spacer(1, 4 * mm),
        tile_table,
        Spacer(1, 5 * mm),
        *[Paragraph(s, st["Normal"]) for s in d["sentences"]],
        Spacer(1, 4 * mm),
        pair,
        Spacer(1, 4 * mm),
        Paragraph(" · ".join(footer), small),
    ]
    doc.build([KeepInFrame(width, A4[1] - 2 * margin, story, mode="shrink")])
    return buf.getvalue()
