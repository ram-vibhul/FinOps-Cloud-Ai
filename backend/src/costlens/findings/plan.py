"""Quick-wins plan: findings sorted into this week / this month / later.

- This week: low effort, low risk, not low confidence (at most ``WEEK_LIMIT``, best first).
- This month: up to medium effort and medium risk, not low confidence.
- Later: the rest (high effort or risk, or low confidence: worth checking first).
Replaced findings (saving 0 after the advice pipeline) are left out. Totals count each
resource once, the way the Savings page does."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from costlens.findings import service
from costlens.models import Finding

WEEK_LIMIT = 10
BUCKETS = ("this_week", "this_month", "later")
LABELS = {"this_week": "This week", "this_month": "This month", "later": "Later"}
WHY = {
    "this_week": "low effort and low risk",
    "this_month": "a little more work or risk",
    "later": "bigger changes, or worth checking first",
}


def bucket_of(f: Finding) -> str:
    if f.confidence == "low":
        return "later"
    if f.effort == "low" and f.risk == "low":
        return "this_week"
    if f.effort in ("low", "medium") and f.risk in ("low", "medium"):
        return "this_month"
    return "later"


def build(db: Session, *, team: str | None = None) -> dict[str, Any]:
    q = select(Finding).where(Finding.status.in_(("open", "approved")), Finding.savings_monthly > 0)
    if team:
        q = q.where(Finding.team == team)
    findings = sorted(db.scalars(q), key=service.score, reverse=True)
    groups: dict[str, list[Finding]] = {b: [] for b in BUCKETS}
    for f in findings:
        b = bucket_of(f)
        if b == "this_week" and len(groups["this_week"]) >= WEEK_LIMIT:
            b = "this_month"
        groups[b].append(f)
    currency = findings[0].currency if findings else None
    out = []
    for b in BUCKETS:
        items = groups[b]
        out.append(
            {
                "key": b,
                "label": LABELS[b],
                "why": WHY[b],
                "count": len(items),
                "savings_monthly": round(sum(service._per_resource(items).values()), 2),
                "items": [
                    {
                        "id": str(f.id),
                        "title": f.title,
                        "category": f.category,
                        "savings_monthly": float(f.savings_monthly),
                        "risk": f.risk,
                        "effort": f.effort,
                        "confidence": f.confidence,
                        "team": f.team,
                        "owner_id": str(f.owner_id) if f.owner_id else None,
                    }
                    for f in items[:50]
                ],
            }
        )
    return {"currency": currency, "buckets": out}
