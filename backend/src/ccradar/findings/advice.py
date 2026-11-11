"""Merge AWS's own advice with CloudCostRadar findings: one finding per resource and action.

When CloudCostRadar already has a finding for the same resource and kind of action, the AWS
advice is shown on it ("also recommended by"). Otherwise the AWS advice becomes a
finding of its own, marked with its source. Purchase advice matches by action alone."""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ccradar.clouds.aws.advice import CLOUD_NAMES, PROVIDER, SOURCES
from ccradar.costs import fx
from ccradar.db.base import utcnow
from ccradar.findings.registry import FindingIn, detector
from ccradar.models import ExternalRecommendation, Finding

GROUPS = {
    "stop": "remove",
    "delete": "remove",
    "rightsize": "rightsize",
    "upgrade": "rightsize",
    "graviton": "graviton",
    "purchase_savings_plan": "purchase_savings_plan",
    "purchase_reservation": "purchase_reservation",
}
REMOVE = {
    "aws.ec2_stopped", "aws.ebs_unattached", "aws.eip_idle", "aws.snapshot_old", "aws.lb_unused",
    "aws.nat_idle", "aws.dms_idle", "aws.sagemaker_idle_endpoints", "aws.vpn_down", "aws.waf_unused",
    "aws.idle_asgs", "aws.eks_idle_nodegroups",
}  # fmt: skip
DETECTOR_GROUP = {
    "aws.previous_generation": "rightsize",
    "aws.graviton": "graviton",
    "aws.lambda_arm64": "graviton",
    "aws.sp_purchase": "purchase_savings_plan",
    "aws.ri_purchase": "purchase_reservation",
    "k8s.rightsizing": "rightsize",
}
PURCHASES = {"purchase_savings_plan", "purchase_reservation"}
ACTIVE = ("open", "approved", "applied")
NAME = "aws.imported_advice"


def group_of(f: Finding) -> str:
    key = f.dedup_key.split(":", 1)[-1]
    if f.detector in REMOVE or key.startswith("idle:"):
        return "remove"
    if f.detector in ("aws.ec2_rightsizing", "aws.rds_rightsizing"):
        return "rightsize"
    return DETECTOR_GROUP.get(f.detector, f.detector)


def keys(resource_id: str | None) -> set[str]:
    """Spellings of one resource: full id/ARN and its last path or colon segment."""
    if not resource_id:
        return set()
    rid = resource_id.strip().lower()
    return {rid, rid.rsplit("/", 1)[-1], rid.rsplit(":", 1)[-1]} - {""}


def _ours(db: Session) -> list[Finding]:
    return list(
        db.scalars(select(Finding).where(Finding.status.in_(ACTIVE), Finding.detector != NAME))
    )


def _index(findings: list[Finding]) -> dict[tuple[str, str], list[Finding]]:
    out: dict[tuple[str, str], list[Finding]] = defaultdict(list)
    for f in findings:
        g = group_of(f)
        if g in PURCHASES:
            out[("*", g)].append(f)
        for k in keys(f.resource_id):
            out[(k, g)].append(f)
    return out


def _match(
    index: dict[tuple[str, str], list[Finding]], rec: ExternalRecommendation
) -> list[Finding]:
    group = GROUPS.get(rec.action, rec.action)
    if group in PURCHASES:
        return index.get(("*", group), [])
    hits: list[Finding] = []
    for k in keys(rec.resource_id):
        hits += [f for f in index.get((k, group), []) if f not in hits]
    return hits


def _converted(
    db: Session, org_id: uuid.UUID, rec: ExternalRecommendation, currency: str
) -> float | None:
    if rec.currency == currency:
        return rec.savings_monthly
    rate = fx.rate(db, utcnow().date(), rec.currency, currency, org_id=org_id)
    return None if rate is None else round(rec.savings_monthly * float(rate), 2)


def _source(
    db: Session, org_id: uuid.UUID, rec: ExternalRecommendation, currency: str
) -> dict[str, Any]:
    return {
        "source": rec.source,
        "label": SOURCES.get(rec.source, rec.source),
        "title": rec.title,
        "savings_monthly": _converted(db, org_id, rec, currency),
    }


@detector(
    NAME, category="imported", title="AWS recommendations CloudCostRadar did not already make"
)
def imported_advice(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    index = _index(_ours(db))
    groups: dict[tuple[str, str], list[ExternalRecommendation]] = defaultdict(list)
    for rec in db.scalars(select(ExternalRecommendation)):
        if _match(index, rec):
            continue
        group = GROUPS.get(rec.action, rec.action)
        rid = (rec.resource_id or "").lower() or rec.source_id
        groups[(rid, group)].append(rec)
    out = []
    for (rid, group), recs in groups.items():
        sources = [_source(db, org_id, r, currency) for r in recs]
        priced = [s for s in sources if s["savings_monthly"] is not None]
        if not priced:
            continue
        best = max(priced, key=lambda s: s["savings_monthly"])
        lead = next(r for r in recs if r.source == best["source"])
        names = sorted({s["label"] for s in sources})
        provider = PROVIDER.get(lead.source, "aws")
        cloud = CLOUD_NAMES[provider]
        out.append(
            FindingIn(
                key=f"{group}:{rid}",
                title=lead.title or f"{group} {rid}",
                description=(
                    f"Recommended by {', '.join(names)}. CloudCostRadar has no matching finding of its "
                    f"own; check the details in the {cloud} console before acting."
                ),
                savings_monthly=round(best["savings_monthly"], 2),
                currency=currency,
                risk="medium",
                effort="medium"
                if (lead.data or {}).get("effort") not in ("VeryLow", "Low")
                else "low",
                overlap=lead.resource_id or rid,
                provider=provider,
                account_id=lead.account_id,
                region=lead.region,
                resource_type=lead.resource_type,
                resource_id=lead.resource_id,
                steps=[
                    f"Open the recommendation in the {cloud} console and review it with the owner."
                ],
                evidence={"imported": True, "sources": sources, "action": group},
            )
        )
    return out


def annotate(db: Session, org_id: uuid.UUID, currency: str) -> int:
    """Record on each CloudCostRadar finding which AWS tools recommend the same thing."""
    findings = _ours(db)
    index = _index(findings)
    agree: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    for rec in db.scalars(select(ExternalRecommendation)):
        for f in _match(index, rec):
            agree[f.id].append(_source(db, org_id, rec, currency))
    changed = 0
    for f in findings:
        new = agree.get(f.id, [])
        if (f.evidence or {}).get("also_recommended_by", []) != new:
            evidence = {k: v for k, v in (f.evidence or {}).items() if k != "also_recommended_by"}
            f.evidence = {**evidence, "also_recommended_by": new} if new else evidence
            changed += 1
    db.flush()
    return changed
