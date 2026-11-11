"""How sure each finding is: high, medium or low, with the reasons in plain words.

Reasons come from what the finding rests on: how it is priced (the bill vs list prices
vs an assumption), how much usage history backs it, how spiky that usage is, and
whether the advice was checked by CloudCostRadar or imported from AWS."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ccradar.models import Finding

LEVELS = ("high", "medium", "low")
ACTIVE = ("open", "approved", "applied")
ASSUMED = {  # detector -> what the estimate assumes
    "aws.nonprod_schedule": "assumes the environment is only needed 12 hours on weekdays",
    "aws.spot_nonprod_capacity": "assumes a 60% Spot discount and workloads that tolerate interruptions",
    "aws.s3_intelligent_tiering": "assumes 20% of Standard storage moves to cheaper tiers",
    "aws.efs_lifecycle": "assumes 30% of the data is not read for 30 days",
    "aws.log_retention": "assumes logs were written at a steady rate",
    "aws.graviton": "uses typical list-price differences; the software must run on Arm",
    "aws.lambda_arm64": "the code must run on Arm",
    "aws.previous_generation": "about 10% is a conservative estimate for instance moves",
    "aws.elasticache_previous_gen": "about 10% is a conservative estimate",
}
FROM_AWS = {"aws.sp_purchase", "aws.ri_purchase"}


def _worst(a: str, b: str) -> str:
    return a if LEVELS.index(a) >= LEVELS.index(b) else b


def assess(f: Finding) -> tuple[str, list[str]]:  # noqa: PLR0912 - one rule per signal
    ev: dict[str, Any] = f.evidence or {}
    level, reasons = "high", []
    basis = ev.get("basis")
    if basis == "list price":
        level = _worst(level, "medium")
        reasons.append("priced from public list prices, not your bill")
    elif basis == "billing" or ev.get("monthly_cost") or ev.get("standard_monthly"):
        reasons.append("priced from your bill")
    days = ev.get("days")
    if isinstance(days, int | float):
        if days < 7:
            level = _worst(level, "low")
            reasons.append(f"only {days:.0f} days of usage data")
        elif days < 14:
            level = _worst(level, "medium")
            reasons.append(f"{days:.0f} days of usage data (14 or more is better)")
        else:
            reasons.append(f"{days:.0f} days of usage data")
    hours = ev.get("hours")
    if f.detector == "k8s.rightsizing" and isinstance(hours, int | float):
        if hours < 72:
            level = _worst(level, "low")
            reasons.append(f"only {hours:.0f} hours of container usage")
        elif hours < 168:
            level = _worst(level, "medium")
            reasons.append(f"{hours:.0f} hours of container usage (a week or more is better)")
        else:
            reasons.append(f"{hours / 24:.0f} days of container usage")
    peak, p95 = ev.get("cpu_max"), ev.get("cpu_p95")
    if (
        isinstance(peak, int | float)
        and isinstance(p95, int | float)
        and p95 > 0
        and peak > 2 * p95
    ):
        level = _worst(level, "medium")
        reasons.append("usage is spiky: the peak is more than twice the usual high")
    if f.detector in ASSUMED:
        level = _worst(level, "medium")
        reasons.append(ASSUMED[f.detector])
    if ev.get("imported"):
        level = _worst(level, "medium")
        reasons.append("recommended by AWS; CloudCostRadar has not checked it against your usage")
    elif f.detector in FROM_AWS:
        level = _worst(level, "medium")
        reasons.append("AWS's purchase recommendation from the last 30 days of usage")
    if ev.get("also_recommended_by"):
        reasons.append("AWS's own tools recommend the same change")
        if level == "medium" and f.detector not in ASSUMED:
            level = "high"
    if (
        f.detector == "aws.ec2_rightsizing"
        and ":size:" in f.dedup_key
        and ev.get("mem_max") is None
    ):
        level = _worst(level, "medium")
        reasons.append("memory use is not measured (install the CloudWatch agent)")
    if not reasons:
        reasons.append("based on the resource's current configuration")
    return level, reasons


def apply(db: Session) -> None:
    for f in db.scalars(select(Finding).where(Finding.status.in_(ACTIVE))):
        level, reasons = assess(f)
        f.confidence = level
        if (f.evidence or {}).get("confidence") != {"level": level, "reasons": reasons}:
            f.evidence = {**(f.evidence or {}), "confidence": {"level": level, "reasons": reasons}}
    db.flush()
