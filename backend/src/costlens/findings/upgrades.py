"""Upgrade advice: what staying on an old version or generation costs, against upgrading.

Extended-support charges come from the bill whenever AWS itemises them (usage types
containing "ExtendedSupport"); otherwise they are estimated from published rates and
labelled as estimates. Aurora follows the RDS calendar per compatible major version."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from costlens.db.base import utcnow
from costlens.findings.aws import MONTH, _common, _resources
from costlens.findings.aws_compute import EKS_STANDARD_END, _usd
from costlens.findings.aws_db import (
    HOURS_MONTH,
    RATE_EARLY,
    RATE_YEAR3,
    _extended_billed,
    support_for,
    vcpus,
)
from costlens.findings.registry import FindingIn, detector
from costlens.inventory.service import resource_costs
from costlens.models import CloudResource, Finding

AURORA_MYSQL_2_ENDS = date(2024, 10, 31)  # Aurora MySQL 2 (MySQL 5.7 compatible)
REDIS_OLD_ENDS = date(2026, 1, 31)  # ElastiCache for Redis OSS 4 and 5
REDIS_PREMIUM = 0.80  # estimate: extended support adds about 80% to the node price (years 1-2)
COMING_UP_DAYS = 180
UPGRADE_DETECTORS = (
    "aws.rds_extended_support", "aws.aurora_extended_support", "aws.eks_extended_support",
    "aws.elasticache_extended_support", "aws.opensearch_extended_support", "aws.previous_generation",
    "aws.elasticache_previous_gen",
)  # fmt: skip


def aurora_support(engine: str, version: str) -> tuple[date, str] | None:
    """(end of standard support, upgrade target) for an Aurora engine version."""
    if engine == "aurora-mysql" and (".mysql_aurora.2." in version or version.startswith("5.7")):
        return AURORA_MYSQL_2_ENDS, "Aurora MySQL 3 (MySQL 8.0 compatible)"
    if engine == "aurora-postgresql":
        s = support_for("postgres", version)
        if s:
            return s.standard_ends, f"Aurora PostgreSQL {s.upgrade_to}"
    return None


def _members(db: Session, cluster: CloudResource) -> list[CloudResource]:
    return [
        d
        for d in _resources(db, "rds_instance")
        if (d.attributes or {}).get("cluster") == cluster.name
    ]


@detector(
    "aws.aurora_extended_support",
    category="database",
    title="Aurora clusters in paid Extended Support",
)
def aurora_extended_support(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    today = utcnow().date()
    usd = _usd(db, org_id, currency)
    out = []
    for c in _resources(db, "rds_cluster"):
        a = c.attributes or {}
        info = aurora_support(str(a.get("engine") or ""), str(a.get("engine_version") or ""))
        if info is None or today <= info[0]:
            continue
        ends, target = info
        members = _members(db, c)
        billed = sum(
            v
            for v in (
                _extended_billed(db, org_id, r, currency)
                for r in [c.resource_id, *[m.resource_id for m in members]]
            )
            if v
        )
        if billed:
            monthly, basis = billed, "billing"
        elif usd is not None and members:
            rate = RATE_YEAR3 if today > ends + timedelta(days=730) else RATE_EARLY
            cpus = sum(
                vcpus(str((m.attributes or {}).get("instance_class") or "")) or 0 for m in members
            )
            monthly, basis = cpus * rate * HOURS_MONTH * usd, "list price"
        else:
            continue
        if monthly < 1:
            continue
        out.append(
            FindingIn(
                key=c.resource_id,
                title=f"Upgrade Aurora cluster {c.name} out of Extended Support",
                description=(
                    f"{a.get('engine')} {a.get('engine_version')} left standard support on {ends:%d %b %Y}. "
                    f"Extended Support costs about {monthly:,.2f} {currency} a month "
                    f"({'from the bill' if basis == 'billing' else 'estimated per vCPU-hour'}). Upgrade to {target}."
                ),
                savings_monthly=round(monthly, 2),
                currency=currency,
                risk="medium",
                effort="high",
                overlap=f"extended:{c.resource_id}",
                **_common(c),
                steps=[
                    "Test the upgrade on a clone (or use a blue/green deployment).",
                    f"Upgrade to {target}; switch over in a quiet window.",
                ],
                evidence={
                    "basis": basis,
                    "standard_end": ends.isoformat(),
                    "upgrade_to": target,
                    "members": len(members),
                },
            )
        )
    return out


@detector(
    "aws.elasticache_extended_support", category="database", title="Caches in paid Extended Support"
)
def elasticache_extended_support(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    today = utcnow().date()
    if today <= REDIS_OLD_ENDS:
        return []
    old = [
        c for c in _resources(db, "elasticache_cluster")
        if str((c.attributes or {}).get("engine") or "") == "redis"
        and str((c.attributes or {}).get("engine_version") or "").split(".")[0] in ("4", "5")
    ]  # fmt: skip
    cost = resource_costs(db, org_id, [c.resource_id for c in old], days=30, currency=currency)
    out = []
    for c in old:
        billed = _extended_billed(db, org_id, c.resource_id, currency)
        if billed:
            monthly, basis = billed, "billing"
        elif c.resource_id in cost:
            monthly, basis = (
                cost[c.resource_id] * MONTH / 30 * REDIS_PREMIUM / (1 + REDIS_PREMIUM),
                "estimate",
            )
        else:
            continue
        if monthly < 1:
            continue
        out.append(
            FindingIn(
                key=c.resource_id,
                title=f"Upgrade cache {c.name} from Redis {(c.attributes or {}).get('engine_version')}",
                description=(
                    f"Redis OSS 4 and 5 left standard support on {REDIS_OLD_ENDS:%d %b %Y}; Extended Support adds "
                    f"about {monthly:,.2f} {currency} a month "
                    f"({'from the bill' if basis == 'billing' else 'estimated; please check your bill'}). "
                    "Upgrade to Valkey 8 (also about 20% cheaper per node) or Redis OSS 7."
                ),
                savings_monthly=round(monthly, 2),
                currency=currency,
                risk="medium",
                effort="medium",
                overlap=f"extended:{c.resource_id}",
                **_common(c),
                steps=[
                    "Upgrade the engine version in a maintenance window (online for replication groups)."
                ],
                commands=[
                    f"aws elasticache modify-replication-group --region {c.region} --replication-group-id "
                    f"{(c.attributes or {}).get('replication_group') or c.name} --engine valkey --engine-version 8.0 "
                    "--apply-immediately"
                ],
                evidence={"basis": basis, "standard_end": REDIS_OLD_ENDS.isoformat()},
            )
        )
    return out


@detector(
    "aws.opensearch_extended_support",
    category="database",
    title="OpenSearch domains in paid Extended Support",
)
def opensearch_extended_support(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    out = []
    for d in _resources(db, "opensearch_domain"):
        billed = _extended_billed(db, org_id, d.resource_id, currency)
        if not billed or billed < 1:
            continue  # only claimed when the bill shows the charge
        version = (d.attributes or {}).get("engine_version")
        out.append(
            FindingIn(
                key=d.resource_id,
                title=f"Upgrade OpenSearch domain {d.name} out of Extended Support",
                description=(
                    f"{version} is billed for Extended Support: {billed:,.2f} {currency} a month on your bill. "
                    "Upgrade to a current OpenSearch version (one major version at a time)."
                ),
                savings_monthly=round(billed, 2),
                currency=currency,
                risk="medium",
                effort="high",
                overlap=f"extended:{d.resource_id}",
                **_common(d),
                steps=["Take a snapshot.", "Upgrade in place, one major version at a time."],
                evidence={"basis": "billing", "engine_version": version},
            )
        )
    return out


def coming_up(db: Session, today: date | None = None) -> list[dict[str, Any]]:
    """Resources whose standard support ends in the next 180 days (no charge yet)."""
    today = today or utcnow().date()
    horizon = today + timedelta(days=COMING_UP_DAYS)
    out = []
    for d in _resources(db, "rds_instance"):
        a = d.attributes or {}
        s = support_for(str(a.get("engine") or ""), str(a.get("engine_version") or ""))
        if s and today < s.standard_ends <= horizon:
            cpus = vcpus(str(a.get("instance_class") or "")) or 0
            out.append(
                {
                    "resource": d.name or d.resource_id,
                    "kind": "RDS",
                    "version": a.get("engine_version"),
                    "ends": s.standard_ends.isoformat(),
                    "upgrade_to": s.upgrade_to,
                    "estimated_monthly_usd": round(
                        cpus * RATE_EARLY * HOURS_MONTH * (2 if a.get("multi_az") else 1), 2
                    ),
                }
            )
    for c in _resources(db, "rds_cluster"):
        a = c.attributes or {}
        info = aurora_support(str(a.get("engine") or ""), str(a.get("engine_version") or ""))
        if info and today < info[0] <= horizon:
            out.append(
                {
                    "resource": c.name,
                    "kind": "Aurora",
                    "version": a.get("engine_version"),
                    "ends": info[0].isoformat(),
                    "upgrade_to": info[1],
                    "estimated_monthly_usd": None,
                }
            )
    for c in _resources(db, "eks_cluster"):
        version = str((c.attributes or {}).get("version") or "")
        end = EKS_STANDARD_END.get(version)
        if end and today < end <= horizon:
            out.append(
                {
                    "resource": c.name,
                    "kind": "EKS",
                    "version": version,
                    "ends": end.isoformat(),
                    "upgrade_to": "the next Kubernetes minor version",
                    "estimated_monthly_usd": round(0.5 * 24 * MONTH, 2),
                }
            )
    return sorted(out, key=lambda r: r["ends"])


def overview(db: Session) -> dict[str, Any]:
    rows = list(
        db.scalars(
            select(Finding).where(
                Finding.detector.in_(UPGRADE_DETECTORS),
                Finding.status.in_(("open", "approved", "applied")),
            )
        )
    )
    items = [
        {
            "id": str(f.id),
            "title": f.title,
            "detector": f.detector,
            "staying_monthly": float(f.savings_monthly),
            "currency": f.currency,
            "basis": (f.evidence or {}).get("basis"),
            "standard_end": (f.evidence or {}).get("standard_end"),
            "upgrade_to": (f.evidence or {}).get("upgrade_to") or (f.evidence or {}).get("target"),
            "effort": f.effort,
        }
        for f in sorted(rows, key=lambda x: -float(x.savings_monthly))
    ]
    return {
        "staying_monthly": round(sum(i["staying_monthly"] for i in items), 2),
        "items": items,
        "coming_up": coming_up(db),
    }
