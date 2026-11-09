"""Schedules for non-production resources running around the clock, and Graviton
suggestions for x86 instances and databases. Savings come from the bill."""

from __future__ import annotations

import re
import uuid

from sqlalchemy.orm import Session

from costlens.findings.aws import MONTH, _common, _label, _resources
from costlens.findings.aws_db import _non_prod
from costlens.findings.registry import FindingIn, detector
from costlens.inventory.service import resource_costs
from costlens.models import CloudResource

MIN_SAVING = 1.0
OFFICE_HOURS = 12 * 5  # hours a week a non-production environment is needed
SCHEDULE_SAVING = 1 - OFFICE_HOURS / (24 * 7)  # 64%
SCHEDULE_TAGS = ("schedule", "instance-scheduler", "auto-stop", "autostop", "office-hours")
GRAVITON_EC2 = {  # x86 family -> Graviton family of the same kind
    "t3": "t4g", "t3a": "t4g",
    "m5": "m7g", "m5a": "m7g", "m6i": "m7g", "m6a": "m7g", "m7i": "m7g", "m7a": "m7g",
    "c5": "c7g", "c5a": "c7g", "c6i": "c7g", "c6a": "c7g", "c7i": "c7g",
    "r5": "r7g", "r5a": "r7g", "r6i": "r7g", "r6a": "r7g", "r7i": "r7g",
}  # fmt: skip
GRAVITON_RDS = {"m5": "m7g", "m6i": "m7g", "r5": "r7g", "r6i": "r7g", "t3": "t4g"}
EC2_GRAVITON_SAVING = 0.18  # list prices are 18-20% lower for the same size
RDS_GRAVITON_SAVING = 0.10
RDS_GRAVITON_ENGINES = re.compile(r"^(postgres|mysql|mariadb|aurora)")


def _scheduled(r: CloudResource) -> bool:
    keys = {k.lower() for k in (r.tags or {})}
    return any(any(word in k for word in SCHEDULE_TAGS) for k in keys)


def _monthly(db: Session, org_id: uuid.UUID, currency: str, ids: list[str]) -> dict[str, float]:
    return {
        k: v * MONTH / 30
        for k, v in resource_costs(db, org_id, ids, days=30, currency=currency).items()
    }


def _stop_command(r: CloudResource, kind: str, name: str) -> str:
    if kind == "instance":
        return f"aws ec2 stop-instances --region {r.region} --instance-ids {r.resource_id}"
    if kind == "database":
        return f"aws rds stop-db-instance --region {r.region} --db-instance-identifier {name}"
    if kind == "Aurora cluster":
        return f"aws rds stop-db-cluster --region {r.region} --db-cluster-identifier {name}"
    return (
        f"aws autoscaling put-scheduled-update-group-action --region {r.region} "
        f"--auto-scaling-group-name {name} --scheduled-action-name evenings "
        "--recurrence '0 20 * * MON-FRI' --min-size 0 --desired-capacity 0"
    )


def _schedule_candidates(db: Session) -> list[tuple[CloudResource, str, list[str]]]:
    candidates: list[tuple[CloudResource, str, list[str]]] = []
    for i in _resources(db, "ec2_instance"):
        if i.state == "running" and "aws:autoscaling:groupName" not in (i.tags or {}):
            candidates.append((i, "instance", [i.resource_id]))
    for d in _resources(db, "rds_instance"):
        a = d.attributes or {}
        if (
            d.state == "available"
            and not a.get("cluster")
            and not str(a.get("engine", "")).startswith("aurora")
        ):
            candidates.append((d, "database", [d.resource_id]))
    for c in _resources(db, "rds_cluster"):
        if c.state == "available" and str((c.attributes or {}).get("engine", "")).startswith(
            "aurora"
        ):
            candidates.append(
                (
                    c,
                    "Aurora cluster",
                    [c.resource_id, *((c.attributes or {}).get("member_arns") or [])],
                )
            )
    for g in _resources(db, "autoscaling_group"):
        a = g.attributes or {}
        if not a.get("eks_nodegroup") and (a.get("desired") or 0) > 0:
            candidates.append((g, "Auto Scaling group", list(a.get("instance_ids") or [])))
    return candidates


@detector(
    "aws.nonprod_schedule", category="schedule", title="Non-production running around the clock"
)
def nonprod_schedule(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    candidates = _schedule_candidates(db)
    candidates = [
        (r, kind, ids) for r, kind, ids in candidates if _non_prod(r) and not _scheduled(r)
    ]
    cost = _monthly(db, org_id, currency, [x for _, _, ids in candidates for x in ids])
    out = []
    for r, kind, ids in candidates:
        monthly = sum(cost.get(x, 0.0) for x in ids)
        saving = round(monthly * SCHEDULE_SAVING, 2)
        if saving < MIN_SAVING:
            continue
        stop = _stop_command(r, kind, r.name or r.resource_id)
        out.append(
            FindingIn(
                key=r.resource_id,
                title=f"Run non-production {kind} {_label(r)} in office hours only",
                description=(
                    f"Looks non-production ({_non_prod(r)}) and runs around the clock. Running it "
                    "12 hours on weekdays cuts its compute cost by about 64% (storage is still "
                    "billed). Tag it with a schedule key once done."
                ),
                savings_monthly=saving,
                currency=currency,
                risk="low",
                effort="low",
                overlap=r.resource_id,
                **_common(r),
                steps=[
                    "Agree the hours with the team that uses it.",
                    "Create a stop schedule for 20:00 and a start schedule for 08:00 on "
                    "weekdays (EventBridge Scheduler or AWS Instance Scheduler).",
                ]
                + (
                    ["RDS restarts a stopped database after 7 days: keep the stop schedule daily."]
                    if kind in ("database", "Aurora cluster")
                    else []
                ),
                commands=[stop],
                evidence={"monthly_cost": round(monthly, 2), "reason": _non_prod(r), "kind": kind},
            )
        )
    return out


@detector(
    "aws.graviton",
    category="rightsizing",
    title="Instances and databases that could run on Graviton",
)
def graviton(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    picks: list[tuple[CloudResource, str, str, float]] = []
    for i in _resources(db, "ec2_instance"):
        a = i.attributes or {}
        itype = str(a.get("instance_type") or "")
        family, _, size = itype.partition(".")
        if (
            i.state == "running"
            and family in GRAVITON_EC2
            and "windows" not in str(a.get("platform") or "").lower()
            and "aws:autoscaling:groupName" not in (i.tags or {})
        ):
            picks.append((i, itype, f"{GRAVITON_EC2[family]}.{size}", EC2_GRAVITON_SAVING))
    for d in _resources(db, "rds_instance"):
        a = d.attributes or {}
        klass = str(a.get("instance_class") or "")
        family, _, size = klass.removeprefix("db.").partition(".")
        if (
            d.state == "available"
            and family in GRAVITON_RDS
            and RDS_GRAVITON_ENGINES.match(str(a.get("engine") or ""))
        ):
            picks.append((d, klass, f"db.{GRAVITON_RDS[family]}.{size}", RDS_GRAVITON_SAVING))
    cost = _monthly(db, org_id, currency, [r.resource_id for r, *_ in picks])
    out = []
    for r, current, target, share in picks:
        monthly = cost.get(r.resource_id, 0.0)
        saving = round(monthly * share, 2)
        if saving < MIN_SAVING:
            continue
        database = r.resource_type == "rds_instance"
        out.append(
            FindingIn(
                key=f"graviton:{r.resource_id}",
                title=f"Move {_label(r)} from {current} to Graviton {target}",
                description=(
                    f"The same size on Graviton (Arm) lists about {share:.0%} cheaper and is "
                    "usually faster. "
                    + (
                        "Supported engines switch with a class change."
                        if database
                        else "Check the software runs on arm64 (most Linux software and "
                        "containers do; rebuild native binaries)."
                    )
                ),
                savings_monthly=saving,
                currency=currency,
                risk="medium",
                effort="medium" if not database else "low",
                overlap=r.resource_id,
                **_common(r),
                steps=(
                    ["Change the instance class in a maintenance window (Multi-AZ fails over)."]
                    if database
                    else [
                        "Build or pull arm64 images and test on a Graviton instance.",
                        "Replace the instance (an arm64 AMI is needed; this is not an in-place change).",
                    ]
                ),
                commands=(
                    [
                        f"aws rds modify-db-instance --region {r.region} --db-instance-identifier "
                        f"{r.name} --db-instance-class {target}"
                    ]
                    if database
                    else []
                ),
                evidence={"current": current, "target": target, "monthly_cost": round(monthly, 2)},
            )
        )
    return out
