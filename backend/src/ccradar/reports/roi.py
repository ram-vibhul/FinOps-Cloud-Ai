"""ROI pack for finance: what CloudCostRadar saved against what it costs.

Verified savings (measured on the bill) and estimates (applied or approved but not yet
measured) are always reported separately; only verified money counts towards the ROI.
Every figure carries its evidence: the finding, the resource, when it was confirmed,
the cost per day before and after, and the months counted."""

from __future__ import annotations

import csv
import io
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ccradar.models import Finding, Organization, PlannedPurchase

MONTH = 30.4


def _months(start: date, end: date) -> list[str]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def settings_of(org: Organization | None) -> dict[str, Any]:
    roi = ((org.settings or {}) if org else {}).get("roi") or {}
    return {
        "monthly_price": roi.get("monthly_price"),
        "currency": roi.get("currency") or (org.default_currency if org else "USD"),
        "since": roi.get("since"),
    }


def build(db: Session, org_id: Any, start: date, end: date) -> dict[str, Any]:
    org = db.get(Organization, org_id)
    cfg = settings_of(org)
    currency = org.default_currency if org else "USD"
    months = _months(start, end)
    verified_rows, estimated_rows = [], []
    for f in db.scalars(
        select(Finding).where(
            Finding.realised_monthly.is_not(None) | Finding.status.in_(("applied", "approved"))
        )
    ):
        m = f.measurement or {}
        history = {h["month"]: float(h["kept_monthly"]) for h in m.get("history", [])}
        confirmed_at = f.verified_at or (
            datetime.fromisoformat(m["regressed_at"]) if m.get("regressed_at") else None
        )
        if f.status == "verified" or history:
            first = (confirmed_at or datetime.now(UTC)).strftime("%Y-%m")
            counted = {
                mo: max(history.get(mo, float(f.realised_monthly or 0)), 0.0)
                for mo in months
                if mo >= first
            }
            if f.status != "verified":  # it came back: only the months it was rechecked count
                counted = {mo: max(v, 0.0) for mo, v in history.items() if mo in months}
            total = round(sum(counted.values()), 2)
            if total > 0:
                verified_rows.append(
                    {
                        "kind": "finding",
                        "title": f.title,
                        "resource": f.resource_id,
                        "category": f.category,
                        "confirmed": f.verified_at.date().isoformat() if f.verified_at else None,
                        "before_daily": m.get("before_daily"),
                        "after_daily": m.get("after_daily"),
                        "months": len(counted),
                        "monthly": float(f.realised_monthly or 0),
                        "total": total,
                        "status": f.status,
                    }
                )
        elif f.status in ("applied", "approved"):
            estimated_rows.append(
                {
                    "kind": "finding",
                    "title": f.title,
                    "resource": f.resource_id,
                    "category": f.category,
                    "status": f.status,
                    "monthly": float(f.savings_monthly),
                }
            )
    for p in db.scalars(select(PlannedPurchase).where(PlannedPurchase.status == "purchased")):
        counted = {
            v["month"]: float(v["saving"]) for v in p.verification or [] if v["month"] in months
        }
        total = round(sum(max(v, 0.0) for v in counted.values()), 2)
        if total > 0:
            verified_rows.append(
                {
                    "kind": "commitment",
                    "title": p.description,
                    "resource": p.commitment_id,
                    "category": "commitments",
                    "confirmed": p.purchased_on.isoformat() if p.purchased_on else None,
                    "before_daily": None,
                    "after_daily": None,
                    "months": len(counted),
                    "monthly": round(total / max(len(counted), 1), 2),
                    "total": total,
                    "status": "purchased",
                }
            )
    verified = round(sum(r["total"] for r in verified_rows), 2)
    estimated_monthly = round(sum(r["monthly"] for r in estimated_rows), 2)
    price = cfg["monthly_price"]
    cost = round(float(price) * len(months), 2) if price else None
    return {
        "organisation": org.name if org else "",
        "currency": currency,
        "period": {"start": start.isoformat(), "end": end.isoformat(), "months": len(months)},
        "verified_total": verified,
        "estimated_monthly": estimated_monthly,
        "product_cost": cost,
        "product_currency": cfg["currency"],
        "roi_multiple": round(verified / cost, 1) if cost else None,
        "net": round(verified - cost, 2)
        if cost is not None and cfg["currency"] == currency
        else None,
        "verified": sorted(verified_rows, key=lambda r: -r["total"]),
        "estimated": sorted(estimated_rows, key=lambda r: -r["monthly"]),
        "notes": [
            "Verified savings are measured on your bill: the resource's cost per day before the change vs after, "
            "rechecked every month; savings that came back count only for the months they held.",
            "Estimates are changes applied or approved but not yet measured; they are not part of the ROI.",
            "Commitment savings are the On-Demand price minus what was paid, on usage the purchase covered.",
        ],
    }


def to_csv(data: dict[str, Any]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        ["CloudCostRadar ROI", data["organisation"], data["period"]["start"], data["period"]["end"]]
    )
    w.writerow(["Verified savings", data["verified_total"], data["currency"]])
    w.writerow(
        [
            "CloudCostRadar cost",
            data["product_cost"] if data["product_cost"] is not None else "not set",
            data["product_currency"],
        ]
    )
    w.writerow(["Estimated monthly (not verified)", data["estimated_monthly"], data["currency"]])
    w.writerow([])
    w.writerow(
        [
            "type",
            "kind",
            "title",
            "resource",
            "category",
            "status",
            "confirmed",
            "cost per day before",
            "cost per day after",
            "months counted",
            "monthly",
            "total",
        ]
    )
    for r in data["verified"]:
        w.writerow(
            [
                "verified",
                r["kind"],
                r["title"],
                r["resource"],
                r["category"],
                r["status"],
                r["confirmed"],
                r["before_daily"],
                r["after_daily"],
                r["months"],
                r["monthly"],
                r["total"],
            ]
        )
    for r in data["estimated"]:
        w.writerow(
            [
                "estimated",
                r["kind"],
                r["title"],
                r["resource"],
                r["category"],
                r["status"],
                "",
                "",
                "",
                "",
                r["monthly"],
                "",
            ]
        )
    return buf.getvalue()


def to_pdf(data: dict[str, Any]) -> bytes:
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
        title="CloudCostRadar ROI pack",
    )
    styles = getSampleStyleSheet()
    cur = data["currency"]

    def money(v: float | None, c: str = cur) -> str:
        return "not set" if v is None else f"{v:,.2f} {c}"

    story: list[Any] = [
        Paragraph(f"CloudCostRadar ROI — {data['organisation']}", styles["Title"]),
        Paragraph(
            f"{data['period']['start']} to {data['period']['end']} ({data['period']['months']} months)",
            styles["Normal"],
        ),
        Spacer(1, 6 * mm),
    ]
    summary = [
        ["Verified savings (on the bill)", money(data["verified_total"])],
        ["CloudCostRadar cost", money(data["product_cost"], data["product_currency"])],
        [
            "Return",
            f"{data['roi_multiple']}x"
            if data["roi_multiple"] is not None
            else "set the CloudCostRadar price",
        ],
        ["Estimated, not yet verified (per month)", money(data["estimated_monthly"])],
    ]
    t = Table(summary, colWidths=[95 * mm, 70 * mm])
    t.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.grey),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ]
        )
    )
    story += [
        t,
        Spacer(1, 6 * mm),
        Paragraph("Verified savings and their evidence", styles["Heading2"]),
    ]
    rows: list[list[Any]] = [["Saving", "Confirmed", "Per day before → after", "Months", "Total"]]
    for r in data["verified"][:60]:
        per_day = (
            f"{r['before_daily']:,.2f} → {r['after_daily']:,.2f}"
            if r["before_daily"] is not None and r["after_daily"] is not None
            else "measured on covered usage"
        )
        rows.append(
            [
                Paragraph(
                    f"{r['title']}<br/><font size=7>{r['resource'] or ''}</font>", styles["Normal"]
                ),
                r["confirmed"] or "",
                per_day,
                str(r["months"]),
                money(r["total"]),
            ]
        )
    v = Table(rows, colWidths=[70 * mm, 22 * mm, 38 * mm, 14 * mm, 30 * mm], repeatRows=1)
    v.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
            ]
        )
    )
    story += [
        v,
        Spacer(1, 6 * mm),
        Paragraph("Estimates (not part of the return)", styles["Heading2"]),
    ]
    est: list[list[Any]] = [["Change", "Status", "Per month"]] + [
        [Paragraph(r["title"], styles["Normal"]), r["status"], money(r["monthly"])]
        for r in data["estimated"][:40]
    ]
    e = Table(est, colWidths=[110 * mm, 25 * mm, 39 * mm], repeatRows=1)
    e.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                ("ALIGN", (2, 0), (2, -1), "RIGHT"),
            ]
        )
    )
    story += [e, Spacer(1, 6 * mm), Paragraph("How these numbers are made", styles["Heading2"])]
    story += [Paragraph(n, styles["Normal"]) for n in data["notes"]]
    doc.build(story)
    return buf.getvalue()
