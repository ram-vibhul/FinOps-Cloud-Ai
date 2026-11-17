"""Investigate a cost change (11.32): "why did cost rise?", with the evidence.

The investigation itself is deterministic, built from the same queries the app uses:

1. compare the period with the one before it, by service; then, inside the service that
   moved most, by account, region and resource;
2. gather the changes Live Radar and other sources recorded on those days, the cost
   anomalies, and the open findings on the resources that grew (the proposed fixes,
   each of which can be approved like any finding);
3. write the facts as a short summary; with AI turned on, Claude rewords it in plain
   words but adds no numbers or causes of its own.

Nothing is changed in any cloud: fixes are proposals for a person to approve.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ccradar.costs import explorer
from ccradar.models import ChangeEvent, CostAnomaly, Finding

TOP = 5


def _movers(
    db: Session,
    org_id: uuid.UUID,
    start: date,
    end: date,
    dim: str,
    filters: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    q = explorer.ExploreQuery(
        start=start, end=end, group_by=[dim], granularity="total", filters=filters or {}, limit=200
    )
    out = explorer.explore(db, org_id, q)
    rows = [
        {
            "dimension": dim,
            "key": g["key"][0],
            "label": g["label"],
            "now": g["total"],
            "before": g["previous"],
            "change": round(g["total"] - g["previous"], 2),
        }
        for g in out["groups"]
    ]
    return sorted(rows, key=lambda r: -r["change"])[:TOP]


def investigate(
    db: Session, org_id: uuid.UUID, start: date, end: date, writer: Any = None
) -> dict[str, Any]:
    overall = explorer.explore(
        db,
        org_id,
        explorer.ExploreQuery(start=start, end=end, group_by=["provider"], granularity="total"),
    )
    now = sum(g["total"] for g in overall["groups"])
    before = sum(g["previous"] for g in overall["groups"])
    services = _movers(db, org_id, start, end, "service")
    top = next((s for s in services if s["change"] > 0), None)
    within: dict[str, list[dict[str, Any]]] = {}
    if top:
        only = {"service": [top["key"]]}
        for dim in ("account", "region", "resource"):
            within[dim] = [r for r in _movers(db, org_id, start, end, dim, only) if r["change"] > 0]
    grown = {r["key"] for r in within.get("resource", [])}

    days = (end - start).days + 1
    changes = [
        e
        for e in db.scalars(
            select(ChangeEvent).where(
                ChangeEvent.occurred_at >= start - timedelta(days=days),
                ChangeEvent.occurred_at < end + timedelta(days=1),
            )
        )
    ]

    def weight(e: ChangeEvent) -> float:
        return float(((e.attributes or {}).get("impact") or {}).get("monthly") or 0)

    related = [
        e
        for e in changes
        if weight(e) > 0 or (top and e.service == top["key"]) or (e.resource_id in grown)
    ]
    related.sort(key=lambda e: (-weight(e), e.occurred_at))
    anomalies = list(
        db.scalars(
            select(CostAnomaly)
            .where(CostAnomaly.last_day >= start, CostAnomaly.day <= end)
            .order_by(CostAnomaly.impact.desc())
            .limit(TOP)
        )
    )
    fixes = [
        f
        for f in db.scalars(select(Finding).where(Finding.status == "open"))
        if (f.resource_id and f.resource_id in grown) or (top and f.service == top["key"])
    ]
    fixes.sort(key=lambda f: -float(f.savings_monthly))

    currency = overall["currency"]
    facts = [
        f"Spend {start} to {end}: {now:,.2f} {currency}, against {before:,.2f} in the "
        f"{days} days before ({now - before:+,.2f})."
    ]
    if top:
        facts.append(f"Largest increase: {top['label']} ({top['change']:+,.2f}).")
        for dim, label in (("account", "account"), ("region", "region"), ("resource", "resource")):
            if within.get(dim):
                r = within[dim][0]
                facts.append(
                    f"Within it, the {label} that grew most: {r['label']} ({r['change']:+,.2f})."
                )
    for e in related[:3]:
        impact = (e.attributes or {}).get("impact") or {}
        cost = f", about {float(impact['monthly']):,.0f} a month" if impact.get("monthly") else ""
        facts.append(f"Change on {e.occurred_at:%d %b}: {e.title} by {e.actor or 'unknown'}{cost}.")
    if fixes:
        best = fixes[0]
        saving = float(best.savings_monthly)
        facts.append(f"Open savings on what grew: {best.title} ({saving:,.2f} a month).")
    summary = " ".join(facts)
    worded = writer.rewrite(summary) if writer is not None else None
    return {
        "currency": currency,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "now": round(now, 2),
        "before": round(before, 2),
        "change": round(now - before, 2),
        "drivers": services,
        "within": within,
        "top_service": top["label"] if top else None,
        "changes": [
            {
                "id": str(e.id),
                "occurred_at": e.occurred_at.isoformat(),
                "title": e.title,
                "actor": e.actor,
                "service": e.service,
                "source": e.source,
                "monthly": ((e.attributes or {}).get("impact") or {}).get("monthly"),
            }
            for e in related[:10]
        ],
        "anomalies": [
            {
                "id": str(a.id),
                "day": a.day.isoformat(),
                "key": a.key,
                "dimension": a.dimension,
                "impact": round(float(a.impact), 2),
            }
            for a in anomalies
        ],
        "fixes": [
            {
                "id": str(f.id),
                "title": f.title,
                "savings_monthly": float(f.savings_monthly),
                "detector": f.detector,
                "resource_id": f.resource_id,
            }
            for f in fixes[:TOP]
        ],
        "facts": summary,
        "summary": worded or summary,
        "worded_by_ai": bool(worded),
    }
