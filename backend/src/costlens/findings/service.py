"""Run detectors, store findings, rank them, and total savings without double counting."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from costlens.db.base import utcnow
from costlens.findings import targets
from costlens.findings.registry import FindingIn, load_all
from costlens.models import Finding, Organization

log = logging.getLogger(__name__)
RISK = {"low": 1.0, "medium": 0.7, "high": 0.4}
EFFORT = {"low": 1.0, "medium": 0.6, "high": 0.35}
CONFIDENCE = {"high": 1.0, "medium": 0.85, "low": 0.6}


def score(f: Finding) -> float:
    """Ranking: monthly saving, discounted for risk, effort and how sure it is."""
    return (
        float(f.savings_monthly)
        * RISK.get(f.risk, 0.5)
        * EFFORT.get(f.effort, 0.5)
        * CONFIDENCE.get(f.confidence or "medium", 0.85)
    )


def _store(
    db: Session, org_id: uuid.UUID, name: str, category: str, items: list[FindingIn]
) -> dict[str, int]:
    now = utcnow()
    existing = {f.dedup_key: f for f in db.scalars(select(Finding).where(Finding.detector == name))}
    seen: set[str] = set()
    created = 0
    for item in items:
        key = f"{name}:{item.key}"[:300]
        seen.add(key)
        f = existing.get(key)
        if f is None:
            f = Finding(
                org_id=org_id,
                detector=name,
                category=category,
                dedup_key=key,
                first_seen_at=now,
                status="open",
            )
            db.add(f)
            created += 1
        elif f.status == "gone":
            f.status, f.resolved_at = "open", None  # it came back
        f.title, f.description = item.title[:300], item.description[:2000]
        f.overlap_key = (item.overlap or item.resource_id or key)[:300]
        f.provider, f.account_id, f.region, f.service = (
            item.provider,
            item.account_id,
            item.region,
            item.service,
        )
        f.resource_type, f.resource_id = item.resource_type, item.resource_id
        f.savings_monthly, f.currency = round(max(item.savings_monthly, 0.0), 2), item.currency
        f.risk, f.effort = item.risk, item.effort
        f.steps, f.commands, f.evidence = item.steps, item.commands, item.evidence
        f.last_seen_at = now
    gone = 0
    for key, f in existing.items():
        if key not in seen and f.status == "open":
            f.status, f.resolved_at = "gone", now
            gone += 1
    db.flush()
    return {"found": len(items), "new": created, "gone": gone}


def run(db: Session, org_id: uuid.UUID, only: list[str] | None = None) -> dict[str, Any]:
    """Run every detector (or ``only``) for one organisation. A failing detector is logged
    and skipped; it does not mark its earlier findings as gone."""
    org = db.get(Organization, org_id)
    currency = org.default_currency if org else "USD"
    results: dict[str, Any] = {}
    for name, d in load_all().items():
        if only and name not in only:
            continue
        try:
            with db.begin_nested():
                items = targets.apply(db, name, d.func(db, org_id, currency))
                results[name] = _store(db, org_id, name, d.category, items)
        except Exception as error:
            log.warning("detector %s failed: %s", name, type(error).__name__)
            results[name] = {"error": type(error).__name__}
    from costlens.findings import pipeline
    from costlens.findings.advice import annotate

    pipeline.apply(db, org_id, currency)
    annotate(db, org_id, currency)
    from costlens.findings import confidence, owners, suppressions

    confidence.apply(db)
    suppressions.apply(db)
    owners.apply(db)
    return results


def stacked(f: Finding) -> bool:
    """Part of an ordered chain for one resource (see pipeline): savings add up."""
    return bool(((f.evidence or {}).get("pipeline") or {}).get("stack"))


def _per_resource(findings: list[Finding]) -> dict[str, float]:
    """Saving per resource: stacked steps add up; anything else counts its largest."""
    chain: dict[str, float] = {}
    best: dict[str, float] = {}
    for f in findings:
        if stacked(f):
            chain[f.overlap_key] = chain.get(f.overlap_key, 0.0) + float(f.savings_monthly)
        else:
            best[f.overlap_key] = max(best.get(f.overlap_key, 0.0), float(f.savings_monthly))
    return {k: max(chain.get(k, 0.0), best.get(k, 0.0)) for k in {*chain, *best}}


def summary(findings: list[Finding]) -> dict[str, Any]:
    """Totals where findings on the same resource count once (the largest saving)."""
    best = _per_resource(findings)
    by_category: dict[str, dict[str, float]] = {}
    for f in findings:
        c = by_category.setdefault(f.category, {"count": 0, "savings_monthly": 0.0})
        c["count"] += 1
    per_cat_best: dict[tuple[str, str], float] = {}
    for f in findings:
        k = (f.category, f.overlap_key)
        per_cat_best[k] = max(per_cat_best.get(k, 0.0), float(f.savings_monthly))
    for (cat, _), v in per_cat_best.items():
        by_category[cat]["savings_monthly"] = round(by_category[cat]["savings_monthly"] + v, 2)
    return {
        "count": len(findings),
        "savings_monthly": round(sum(best.values()), 2),
        "overlapping": len(findings) - len(best),
        "by_category": by_category,
    }
