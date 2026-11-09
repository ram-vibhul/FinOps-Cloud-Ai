"""Client re-bill statements (8.10): a PDF in the partner's branding.

Shows only what the client pays (the re-billed amounts per service and the total).
The partner's own cost, markup and margin never appear on it."""

from __future__ import annotations

import io
from typing import Any


def to_pdf(
    partner_name: str,
    color: str,
    client: str,
    month_label: str,
    currency: str,
    lines: list[tuple[str, float]],
    total: float,
    *,
    compress: bool = True,
) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    brand = colors.HexColor(color)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=f"{partner_name} statement {month_label}",
        pageCompression=1 if compress else 0,
    )
    st = getSampleStyleSheet()
    title = st["Title"].clone("brand", textColor=brand, alignment=0)
    story: list[Any] = [
        Paragraph(partner_name, title),
        Paragraph(f"Cloud services statement for <b>{client}</b>, {month_label}", st["Normal"]),
        Spacer(1, 6 * mm),
    ]
    rows: list[list[Any]] = [["Service", f"Amount ({currency})"]]
    rows += [[Paragraph(s, st["Normal"]), f"{v:,.2f}"] for s, v in lines]
    rows.append(["Total", f"{total:,.2f}"])
    t = Table(rows, colWidths=[120 * mm, 50 * mm], repeatRows=1)
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), brand),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("LINEABOVE", (0, -1), (-1, -1), 0.75, brand),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("LINEBELOW", (0, 1), (-1, -2), 0.25, colors.lightgrey),
            ]
        )
    )
    story += [
        t,
        Spacer(1, 6 * mm),
        Paragraph(f"Questions about this statement? Contact {partner_name}.", st["Normal"]),
    ]
    doc.build(story)
    return buf.getvalue()
