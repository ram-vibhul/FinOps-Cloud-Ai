"""Make advice for one resource consistent, in a fixed order:

    remove (idle/stopped) > size > generation / Graviton > Spot > schedule > commitments

Removing a resource replaces every other suggestion for it. Graviton replaces an older
x86 generation change (they would contradict). The rest stack: each step's saving is
sized on the cost left after the steps before it, so totals add up honestly, and
Graviton targets the size the downsizing picks. Savings Plan purchases are then sized
on the compute spend that is left after those changes."""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from costlens.findings.advice import REMOVE
from costlens.findings.commitments import Converter
from costlens.models import Finding

ACTIVE = ("open", "approved")
ORDER = ("remove", "size", "generation", "spot", "schedule")
GENERATION = {
    "aws.previous_generation",
    "aws.graviton",
    "aws.lambda_arm64",
    "aws.elasticache_previous_gen",
}
COMPUTE_TYPES = {"ec2_instance", "autoscaling_group", "eks_nodegroup", "lambda_function"}
HOURS = 730


def step_of(f: Finding) -> str | None:
    key = f.dedup_key.split(":", 1)[-1]
    if f.detector in REMOVE or key.startswith("idle:"):
        return "remove"
    sized = f.detector in ("aws.ec2_rightsizing", "aws.rds_rightsizing") and key.startswith("size:")
    if sized or f.detector == "aws.elasticache_rightsizing":
        return "size"
    if f.detector in GENERATION and not key.startswith("gp3:"):
        return "generation"
    if f.detector == "aws.spot_nonprod_capacity":
        return "spot"
    if f.detector == "aws.nonprod_schedule":
        return "schedule"
    return None


def _original(f: Finding) -> tuple[float, str]:
    """Savings and title as the detector made them (a previous pass may have changed them)."""
    p = (f.evidence or {}).get("pipeline") or {}
    return float(p.get("original", f.savings_monthly)), str(p.get("original_title", f.title))


def _set(f: Finding, saving: float, title: str, info: dict[str, Any]) -> None:
    original, original_title = _original(f)
    evidence = {k: v for k, v in (f.evidence or {}).items() if k != "pipeline"}
    f.evidence = {
        **evidence,
        "pipeline": {**info, "original": original, "original_title": original_title},
    }
    f.savings_monthly, f.title = round(max(saving, 0.0), 2), title[:300]


def _base(members: list[Finding]) -> float | None:
    for f in members:
        cost = (f.evidence or {}).get("monthly_cost")
        if cost:
            return float(cost)
    return None


def _graviton_title(f: Finding, size_target: str | None, title: str) -> str:
    target = str((f.evidence or {}).get("target") or "")
    if f.detector != "aws.graviton" or not size_target or "." not in target:
        return title
    prefix = "db." if target.startswith("db.") else ""
    family = target.removeprefix(prefix).split(".", 1)[0]
    size = size_target.removeprefix("db.").split(".", 1)[-1]
    new = f"{prefix}{family}.{size}"
    return f"After downsizing, move {f.resource_id} to Graviton {new}"


def _supersede(members: list[Finding], candidates: list[Finding]) -> Finding:
    """Keep the largest candidate; every other member is replaced by it."""
    keep = max(candidates, key=lambda f: _original(f)[0])
    for f in members:
        if f is not keep:
            _set(f, 0.0, _original(f)[1], {"superseded_by": keep.title, "stack": False})
    return keep


def _chain(members: list[Finding]) -> None:
    by_step: dict[str, list[Finding]] = defaultdict(list)
    for f in members:
        by_step[step_of(f) or ""].append(f)
    if by_step.get("remove"):
        keep = _supersede(members, by_step["remove"])
        _set(keep, _original(keep)[0], _original(keep)[1], {"step": "remove", "stack": True})
        return
    for step in ("generation", "size"):  # one suggestion per step: the largest wins
        if len(by_step.get(step, [])) > 1:
            by_step[step] = [_supersede(by_step[step], by_step[step])]
    base = _base(members)
    if base is None or base <= 0:
        return
    remaining = base
    size_target = next(
        (str((f.evidence or {}).get("target")) for f in by_step.get("size", [])), None
    )
    order = []
    for step in ORDER[1:]:
        for f in by_step.get(step, []):
            original, title = _original(f)
            share = min(original / base, 1.0)
            saving = remaining * share
            info = {"step": step, "sized_on": round(remaining, 2), "stack": True}
            _set(f, saving, _graviton_title(f, size_target, title), info)
            remaining -= saving
            order.append(f.title)
    for f in members:
        if (f.evidence or {}).get("pipeline", {}).get("stack"):
            f.evidence = {**f.evidence, "pipeline": {**f.evidence["pipeline"], "order": order}}


def _commitments(db: Session, org_id: uuid.UUID, currency: str, findings: list[Finding]) -> None:
    """Shrink Savings Plan purchases by the compute spend the other changes remove."""
    removed: dict[str, float] = {}
    for f in findings:
        if f.resource_type in COMPUTE_TYPES and step_of(f) and float(f.savings_monthly) > 0:
            removed[f.overlap_key] = removed.get(f.overlap_key, 0.0) + float(f.savings_monthly)
    pending = sum(removed.values())
    convert = Converter(db, org_id, currency)
    for f in findings:
        if f.detector != "aws.sp_purchase":
            continue
        original, title = _original(f)
        ev = f.evidence or {}
        hourly, pct = float(ev.get("hourly_commitment") or 0), float(ev.get("savings_pct") or 0)
        covered = convert(hourly * HOURS / max(1 - pct / 100, 0.01), str(ev.get("currency", "USD")))
        if not covered or pending <= 0:
            continue
        left = max(covered - pending, 0.0) / covered
        new_hourly = hourly * left
        info = {
            "step": "commitments",
            "stack": False,
            "aws_hourly": hourly,
            "sized_hourly": round(new_hourly, 2),
            "compute_changes_monthly": round(pending, 2),
        }
        if new_hourly < 0.01:
            info["superseded_by"] = (
                "the right-sizing and clean-up suggestions (nothing steady left to cover)"
            )
            _set(f, 0.0, title, info)
        else:
            cur = ev.get("currency", "USD")
            new_title = title.replace(f"{hourly:.2f} {cur}/hour", f"{new_hourly:.2f} {cur}/hour")
            _set(f, original * left, new_title, info)


def apply(db: Session, org_id: uuid.UUID, currency: str) -> None:
    findings = list(db.scalars(select(Finding).where(Finding.status.in_(ACTIVE))))
    groups: dict[str, list[Finding]] = defaultdict(list)
    for f in findings:
        if step_of(f):
            groups[f.overlap_key].append(f)
    for members in groups.values():
        if len(members) > 1:
            _chain(members)
        else:  # alone: undo any earlier adjustment
            f = members[0]
            if (f.evidence or {}).get("pipeline", {}).get("step") != "commitments":
                original, title = _original(f)
                if (f.evidence or {}).get("pipeline"):
                    _set(f, original, title, {"step": step_of(f), "stack": True})
    _commitments(db, org_id, currency, findings)
    db.flush()
