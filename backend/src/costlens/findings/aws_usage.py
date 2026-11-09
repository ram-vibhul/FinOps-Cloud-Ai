"""EC2 and RDS right-sizing from CloudWatch usage (never below the observed peak plus
headroom: a size down is suggested only when the peak would still fit with room)."""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from costlens.db.base import utcnow
from costlens.findings.aws import MONTH, _common, _label, _resources
from costlens.findings.registry import FindingIn, detector
from costlens.inventory.service import resource_costs
from costlens.models import ResourceMetric

MIN_DAYS = 7
DOWNSIZE_PEAK = 40.0  # % of current capacity: halved, the peak is still <= 80%
IDLE_CPU = 5.0
HALF = {
    "xlarge": "large",
    "2xlarge": "xlarge",
    "4xlarge": "2xlarge",
    "8xlarge": "4xlarge",
    "16xlarge": "8xlarge",
    "24xlarge": "12xlarge",
    "32xlarge": "16xlarge",
    "48xlarge": "24xlarge",
    "large": "medium",
    "medium": "small",
    "small": "micro",
    "micro": "nano",
}
# RDS memory (GiB) for 'large' by family; it doubles with each size up.
RDS_LARGE_GIB = {"t": 8.0, "m": 8.0, "r": 16.0, "x": 32.0}
RDS_SIZE_FACTOR = {
    "micro": 0.125,
    "small": 0.25,
    "medium": 0.5,
    "large": 1,
    "xlarge": 2,
    "2xlarge": 4,
    "4xlarge": 8,
    "8xlarge": 16,
    "12xlarge": 24,
    "16xlarge": 32,
    "24xlarge": 48,
}


def smaller(instance_type: str) -> str | None:
    """One size down in the same family, if that size exists."""
    prefix = "db." if instance_type.startswith("db.") else ""
    family, _, size = instance_type.removeprefix(prefix).partition(".")
    nxt = HALF.get(size)
    if not nxt:
        return None
    burstable = family.startswith("t")
    graviton = bool(re.search(r"\dg", family)) or family == "a1"
    if nxt in ("medium",) and not (burstable or graviton):
        return None  # m5/c5/r5... start at large
    if nxt in ("small", "micro", "nano") and not burstable:
        return None
    return f"{prefix}{family}.{nxt}"


def rds_memory_gib(instance_class: str) -> float | None:
    family, _, size = instance_class.removeprefix("db.").partition(".")
    base = RDS_LARGE_GIB.get(family[:1])
    factor = RDS_SIZE_FACTOR.get(size)
    return base * factor if base and factor else None


def _usage(db: Session, ids: list[str]) -> dict[str, dict[str, dict[str, float]]]:
    """Per resource and metric over the last 14 days: days, avg, max, min, p95 (worst day)."""
    rows = db.scalars(
        select(ResourceMetric).where(
            ResourceMetric.resource_id.in_(ids),
            ResourceMetric.day >= (utcnow() - timedelta(days=14)).date(),
        )
    )
    grouped: dict[str, dict[str, list[ResourceMetric]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        grouped[r.resource_id][r.metric].append(r)
    return {
        rid: {
            m: {
                "days": len(v),
                "avg": sum(x.avg for x in v) / len(v),
                "max": max(x.max for x in v),
                "min": min(x.min for x in v),
                "p95": max(x.p95 for x in v),
            }
            for m, v in metrics.items()
        }
        for rid, metrics in grouped.items()
    }


def _monthly(db: Session, org_id: uuid.UUID, currency: str, ids: list[str]) -> dict[str, float]:
    return {
        k: v * MONTH / 30
        for k, v in resource_costs(db, org_id, ids, days=30, currency=currency).items()
    }


@detector("aws.ec2_rightsizing", category="rightsizing", title="Over-sized or idle EC2 instances")
def ec2_rightsizing(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    instances = [i for i in _resources(db, "ec2_instance") if i.state == "running"]
    ids = [i.resource_id for i in instances]
    usage, cost = _usage(db, ids), _monthly(db, org_id, currency, ids)
    out = []
    for inst in instances:
        u, monthly = usage.get(inst.resource_id, {}), cost.get(inst.resource_id)
        cpu, mem = u.get("cpu_pct"), u.get("mem_pct")
        itype = (inst.attributes or {}).get("instance_type") or ""
        if not cpu or cpu["days"] < MIN_DAYS or not monthly:
            continue  # needs more data
        if "aws:autoscaling:groupName" in (inst.tags or {}):
            continue  # the group replaces it: judged per group (idle groups, Spot)
        where = f"--region {inst.region} --instance-ids {inst.resource_id}"
        evidence = {
            "days": cpu["days"],
            "cpu_max": round(cpu["max"], 1),
            "cpu_p95": round(cpu["p95"], 1),
            "cpu_avg": round(cpu["avg"], 1),
            "instance_type": itype,
            "mem_max": round(mem["max"], 1) if mem else None,
            "monthly_cost": round(monthly, 2),
        }
        if cpu["max"] < IDLE_CPU and (not mem or mem["max"] < 20):
            out.append(
                FindingIn(
                    key=f"idle:{inst.resource_id}",
                    title=f"Stop or remove idle instance {_label(inst)}",
                    description=(
                        f"CPU never went above {cpu['max']:.1f}% in {cpu['days']} days. If it only "
                        "serves occasional jobs, stop it between runs or move them to "
                        "Lambda/Fargate."
                    ),
                    savings_monthly=round(monthly, 2),
                    currency=currency,
                    risk="medium",
                    effort="medium",
                    overlap=inst.resource_id,
                    **_common(inst),
                    steps=[
                        "Confirm with the owner what it does.",
                        "Create an AMI, then stop or terminate it.",
                    ],
                    commands=[f"aws ec2 stop-instances {where}"],
                    evidence=evidence,
                )
            )
            continue
        target = smaller(itype)
        mem_ok = not mem or mem["max"] <= 42.5  # doubled, still <= 85%
        if target and cpu["max"] <= DOWNSIZE_PEAK and mem_ok:
            note = (
                " Burstable type: check CPU credits after the change."
                if itype.startswith("t")
                else ""
            )
            mem_text = (
                f", memory peaked at {mem['max']:.0f}%"
                if mem
                else " (memory not measured: install the CloudWatch agent to be sure)"
            )
            out.append(
                FindingIn(
                    key=f"size:{inst.resource_id}",
                    title=f"Downsize {_label(inst)} from {itype} to {target}",
                    description=(
                        f"CPU peaked at {cpu['max']:.0f}% (95th percentile {cpu['p95']:.0f}%) in "
                        f"{cpu['days']} days{mem_text}. Half the size keeps the peak under "
                        f"80%.{note}"
                    ),
                    savings_monthly=round(monthly * 0.5, 2),
                    currency=currency,
                    risk="medium",
                    effort="medium",
                    overlap=inst.resource_id,
                    **_common(inst),
                    steps=[
                        "Pick a quiet window (the change needs a stop/start).",
                        f"Change the type to {target}.",
                        "Watch CPU and latency for a week.",
                    ],
                    commands=[
                        f"aws ec2 stop-instances {where}",
                        f"aws ec2 modify-instance-attribute --region {inst.region} "
                        f"--instance-id {inst.resource_id} --instance-type {target}",
                        f"aws ec2 start-instances {where}",
                    ],
                    evidence={**evidence, "target": target},
                )
            )
    return out


@detector("aws.rds_rightsizing", category="database", title="Over-sized or idle RDS databases")
def rds_rightsizing(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    dbs = [d for d in _resources(db, "rds_instance") if d.state == "available"]
    ids = [d.resource_id for d in dbs]
    usage, cost = _usage(db, ids), _monthly(db, org_id, currency, ids)
    out = []
    for d in dbs:
        u, monthly = usage.get(d.resource_id, {}), cost.get(d.resource_id)
        cpu, conns, free = u.get("cpu_pct"), u.get("connections"), u.get("free_memory_bytes")
        klass = (d.attributes or {}).get("instance_class") or ""
        ident = d.name or d.resource_id
        db_where = f"--region {d.region} --db-instance-identifier {ident}"
        if not cpu or cpu["days"] < MIN_DAYS or not monthly:
            continue
        total = rds_memory_gib(klass)
        used_pct = (100 * (1 - free["min"] / (total * 2**30))) if (free and total) else None
        evidence: dict[str, Any] = {
            "days": cpu["days"],
            "cpu_max": round(cpu["max"], 1),
            "instance_class": klass,
            "connections_max": conns["max"] if conns else None,
            "memory_used_max_pct": round(used_pct, 1) if used_pct is not None else None,
            "monthly_cost": round(monthly, 2),
        }
        if conns and conns["days"] >= MIN_DAYS and conns["max"] == 0:
            out.append(
                FindingIn(
                    key=f"idle:{d.resource_id}",
                    title=f"Stop or delete idle database {ident}",
                    description=f"No connections at all in {conns['days']} days.",
                    savings_monthly=round(monthly, 2),
                    currency=currency,
                    risk="high",
                    effort="medium",
                    overlap=d.resource_id,
                    **_common(d),
                    steps=[
                        "Confirm nobody uses it.",
                        "Take a final snapshot.",
                        "Delete it (or stop it: RDS restarts stopped databases after 7 days).",
                    ],
                    commands=[
                        f"aws rds delete-db-instance {db_where} "
                        f"--final-db-snapshot-identifier {ident}-final"
                    ],
                    evidence=evidence,
                )
            )
            continue
        target = smaller(klass)
        if target and cpu["max"] <= DOWNSIZE_PEAK and used_pct is not None and used_pct <= 40:
            out.append(
                FindingIn(
                    key=f"size:{d.resource_id}",
                    title=f"Downsize database {ident} from {klass} to {target}",
                    description=(
                        f"CPU peaked at {cpu['max']:.0f}% and memory use at {used_pct:.0f}% in "
                        f"{cpu['days']} days; half the size keeps both under 80%."
                    ),
                    savings_monthly=round(monthly * 0.5, 2),
                    currency=currency,
                    risk="medium",
                    effort="medium",
                    overlap=d.resource_id,
                    **_common(d),
                    steps=[
                        "Schedule it in the maintenance window (Multi-AZ fails over with a "
                        "short blip).",
                        f"Change the class to {target}.",
                        "Watch CPU, memory and latency for a week.",
                    ],
                    commands=[
                        f"aws rds modify-db-instance {db_where} --db-instance-class {target}"
                    ],
                    evidence={**evidence, "target": target},
                )
            )
    return out
