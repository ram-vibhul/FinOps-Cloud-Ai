""" "Doesn't apply to us": rules that dismiss matching findings, with a reason and a
scope, and reopen them when the rule is removed. Plus per-check noise statistics."""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ccradar.db.base import utcnow
from ccradar.findings import workflow
from ccradar.findings.registry import load_all
from ccradar.models import CloudResource, Finding, Suppression

REASONS = {
    "not_applicable": "Doesn't apply to us",
    "accepted_risk": "We accept this cost",
    "false_positive": "The finding is wrong",
    "planned": "Already planned",
    "other": "Other",
}
SCOPES = ("finding", "resource", "tag", "account", "check")
WIDE = {"tag", "account", "check"}  # admin only
OPEN = ("open", "approved")


def _active(db: Session) -> list[Suppression]:
    now = utcnow()
    rows = []
    for s in db.scalars(select(Suppression)):
        end = s.expires_at
        if end is not None and end.tzinfo is None:
            end = end.replace(tzinfo=now.tzinfo)
        if end is None or end > now:
            rows.append(s)
    return rows


def matches(s: Suppression, f: Finding, tags: dict[str, str] | None) -> bool:
    if s.detector not in ("*", f.detector):
        return False
    if s.scope == "finding":
        return f.dedup_key == s.value
    if s.scope == "resource":
        return bool(f.resource_id) and f.resource_id == s.value
    if s.scope == "account":
        return f.account_id == s.value
    if s.scope == "tag":
        key, _, value = s.value.partition("=")
        lowered = {k.lower(): str(v).lower() for k, v in (tags or {}).items()}
        return lowered.get(key.lower()) == value.lower()
    return s.scope == "check"


def _tags(db: Session, findings: list[Finding]) -> dict[str, dict[str, str]]:
    ids = [f.resource_id for f in findings if f.resource_id]
    if not ids:
        return {}
    return dict(
        db.execute(
            select(CloudResource.resource_id, CloudResource.tags).where(
                CloudResource.resource_id.in_(ids)
            )
        ).all()
    )


def apply(db: Session) -> int:
    """Dismiss open findings an active rule covers (after every detector run)."""
    rules = _active(db)
    if not rules:
        return 0
    findings = list(db.scalars(select(Finding).where(Finding.status.in_((*OPEN, "dismissed")))))
    tags = _tags(db, findings)
    done = 0
    for f in findings:
        rule = next((r for r in rules if matches(r, f, tags.get(f.resource_id or ""))), None)
        if rule is None:
            continue
        if f.status == "dismissed":
            # Detector runs rewrite the evidence: keep the link to the rule that dismissed it.
            if (
                (f.evidence or {}).get("suppressed_by") is None
                and f.status_note
                and f.status_note.startswith(REASONS.get(rule.reason, rule.reason))
            ):
                f.evidence = {**(f.evidence or {}), "suppressed_by": str(rule.id)}
            continue
        note = f"{REASONS.get(rule.reason, rule.reason)}{': ' + rule.note if rule.note else ''}"
        f.status, f.status_note, f.status_by_id = "dismissed", note[:1000], rule.created_by_id
        f.evidence = {**(f.evidence or {}), "suppressed_by": str(rule.id)}
        workflow.event(
            db,
            f,
            "status",
            None,
            f"Dismissed by rule: {note}",
            {"to": "dismissed", "rule": str(rule.id)},
        )
        done += 1
    db.flush()
    return done


def remove(db: Session, rule: Suppression, user_id: uuid.UUID | None) -> int:
    """Delete a rule and reopen the findings it dismissed (unless another rule covers them)."""
    rid = str(rule.id)
    db.delete(rule)
    db.flush()
    others = _active(db)
    reopened = 0
    candidates = [
        f
        for f in db.scalars(select(Finding).where(Finding.status == "dismissed"))
        if (f.evidence or {}).get("suppressed_by") == rid
    ]
    tags = _tags(db, candidates)
    for f in candidates:
        evidence = {k: v for k, v in (f.evidence or {}).items() if k != "suppressed_by"}
        other = next((o for o in others if matches(o, f, tags.get(f.resource_id or ""))), None)
        if other is not None:  # still covered by another rule: it stays dismissed under that one
            f.evidence = {**evidence, "suppressed_by": str(other.id)}
            continue
        f.evidence = evidence
        f.status, f.status_note, f.status_by_id = "open", None, user_id
        workflow.event(
            db,
            f,
            "status",
            user_id,
            "The rule was removed: reopened",
            {"from": "dismissed", "to": "open"},
        )
        reopened += 1
    db.flush()
    return reopened


def noise(db: Session) -> list[dict[str, Any]]:
    """Per check: how many findings people dismissed (and why) vs acted on."""
    counts: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"total": 0, "dismissed": 0, "acted": 0, "open": 0, "reasons": defaultdict(int)}
    )
    for f in db.scalars(select(Finding)):
        c = counts[f.detector]
        c["total"] += 1
        if f.status == "dismissed":
            c["dismissed"] += 1
        elif f.status in ("approved", "applied", "verified"):
            c["acted"] += 1
        elif f.status == "open":
            c["open"] += 1
    rule_reason = {str(s.id): s.reason for s in db.scalars(select(Suppression))}
    for f in db.scalars(select(Finding).where(Finding.status == "dismissed")):
        reason = rule_reason.get(str((f.evidence or {}).get("suppressed_by")), "manual")
        counts[f.detector]["reasons"][reason] += 1
    titles = {d.name: d.title for d in load_all().values()}
    out = []
    for name, c in counts.items():
        decided = c["dismissed"] + c["acted"]
        out.append(
            {
                "detector": name,
                "title": titles.get(name, name),
                "total": c["total"],
                "open": c["open"],
                "dismissed": c["dismissed"],
                "acted": c["acted"],
                "dismissed_pct": round(100 * c["dismissed"] / decided, 1) if decided else None,
                "reasons": dict(c["reasons"]),
                "noisy": decided >= 5 and c["dismissed"] / decided >= 0.6,
            }
        )
    return sorted(out, key=lambda r: (-(r["dismissed_pct"] or 0), -r["total"]))
