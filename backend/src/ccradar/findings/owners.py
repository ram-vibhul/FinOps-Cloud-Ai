"""Owner routing: rules give each finding a team and/or a person, by account, tag,
virtual tag, service, Kubernetes namespace or finding category. The lowest priority
number wins; an owner a person chose by hand is never overwritten."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ccradar.db.base import Base, utcnow
from ccradar.findings import workflow
from ccradar.models import CloudResource, Finding, OwnerRule

ACTIVE = ("open", "approved", "applied")
KINDS = ("account", "tag", "vtag", "service", "namespace", "category")


def _namespace(f: Finding) -> str | None:
    if f.provider == "kubernetes" and f.resource_id:
        parts = f.resource_id.split("/")
        return parts[1] if len(parts) >= 3 else None
    return None


def matches(rule: OwnerRule, f: Finding, tags: dict[str, str], vtags: dict[str, str]) -> bool:  # noqa: PLR0911
    value = rule.value.strip().lower()
    if rule.kind == "account":
        return (f.account_id or "").lower() == value
    if rule.kind == "tag":
        return {k.lower(): str(v).lower() for k, v in tags.items()}.get(rule.key.lower()) == value
    if rule.kind == "vtag":
        return {k.lower(): str(v).lower() for k, v in vtags.items()}.get(rule.key.lower()) == value
    if rule.kind == "service":
        return value in (f.service or "").lower() or value == (f.provider or "").lower()
    if rule.kind == "namespace":
        return (_namespace(f) or "").lower() == value
    if rule.kind == "category":
        return f.category.lower() == value
    return False


def _context(db: Session, findings: list[Finding]) -> tuple[dict[str, dict], dict[str, dict]]:
    ids = [f.resource_id for f in findings if f.resource_id]
    if not ids:
        return {}, {}
    tags = dict(
        db.execute(
            select(CloudResource.resource_id, CloudResource.tags).where(
                CloudResource.resource_id.in_(ids)
            )
        ).all()
    )
    t = Base.metadata.tables["cost_line_items"]
    since = utcnow().date() - timedelta(days=30)
    vtags: dict[str, dict] = {}
    for start in range(0, len(ids), 500):
        for rid, vt in db.execute(
            select(t.c.resource_id, t.c.vtags).where(
                t.c.resource_id.in_(ids[start : start + 500]), t.c.charge_date >= since
            )
        ):
            if vt and rid not in vtags:
                vtags[rid] = vt
    return tags, vtags


def apply(db: Session) -> int:
    rules = sorted(db.scalars(select(OwnerRule)), key=lambda r: (r.priority, r.name))
    findings = [
        f
        for f in db.scalars(select(Finding).where(Finding.status.in_(ACTIVE)))
        if f.owner_source != "person"
    ]
    tags, vtags = _context(db, findings)
    changed = 0
    for f in findings:
        rule = next(
            (
                r
                for r in rules
                if matches(
                    r, f, tags.get(f.resource_id or "") or {}, vtags.get(f.resource_id or "") or {}
                )
            ),
            None,
        )
        team = rule.team if rule else None
        owner = rule.owner_id if rule else None
        if (f.team, f.owner_id, f.owner_source) == (team, owner, "rule" if rule else None):
            continue
        f.team, f.owner_id, f.owner_source = team, owner, ("rule" if rule else None)
        workflow.event(
            db,
            f,
            "owner",
            None,
            f"Routed by rule {rule.name}" if rule else "No owner rule matches any more",
            {"owner_id": str(owner) if owner else None, "team": team},
        )
        changed += 1
    db.flush()
    return changed


def preview(db: Session, rule: OwnerRule) -> dict[str, Any]:
    findings = list(db.scalars(select(Finding).where(Finding.status.in_(ACTIVE))))
    tags, vtags = _context(db, findings)
    hits = [
        f
        for f in findings
        if matches(
            rule, f, tags.get(f.resource_id or "") or {}, vtags.get(f.resource_id or "") or {}
        )
    ]
    return {
        "findings": len(hits),
        "savings_monthly": round(sum(float(f.savings_monthly) for f in hits), 2),
    }
