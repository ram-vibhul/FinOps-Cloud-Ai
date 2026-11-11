"""Database checks: paid RDS Extended Support and Multi-AZ on non-production databases.
(Over-sized and idle databases: findings/aws_usage.py.)"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ccradar.costs import fx
from ccradar.db.base import Base, utcnow
from ccradar.findings.aws import MONTH, _common, _resources
from ccradar.findings.registry import FindingIn, detector
from ccradar.inventory.service import resource_costs
from ccradar.models import CloudResource

HOURS_MONTH = 24 * MONTH


@dataclass(frozen=True)
class Support:
    engine: str
    major: str
    standard_ends: date
    upgrade_to: str


# End of RDS standard support per major version, as published by AWS (RDS user guide,
# "Amazon RDS Extended Support"). Keep in sync with AWS announcements.
EXTENDED_SUPPORT = [
    Support("mysql", "5.7", date(2024, 2, 29), "8.4"),
    Support("mysql", "8.0", date(2026, 7, 31), "8.4"),
    Support("postgres", "11", date(2024, 2, 29), "16"),
    Support("postgres", "12", date(2025, 2, 28), "16"),
    Support("postgres", "13", date(2026, 2, 28), "16"),
]
# USD per vCPU-hour (us-east-1): years 1-2, then year 3.
RATE_EARLY, RATE_YEAR3 = 0.100, 0.200
VCPUS = {
    "micro": 2,
    "small": 2,
    "medium": 2,
    "large": 2,
    "xlarge": 4,
    "2xlarge": 8,
    "4xlarge": 16,
    "8xlarge": 32,
    "12xlarge": 48,
    "16xlarge": 64,
    "24xlarge": 96,
    "32xlarge": 128,
}
NON_PROD = (
    "dev",
    "develop",
    "development",
    "test",
    "testing",
    "qa",
    "staging",
    "stage",
    "sandbox",
    "nonprod",
    "non-prod",
    "uat",
    "preview",
)


def _major(engine: str, version: str) -> str:
    """MySQL majors are two numbers (5.7, 8.0); PostgreSQL majors are one (11, 16)."""
    parts = version.split(".")
    return ".".join(parts[:2]) if engine == "mysql" else parts[0]


def support_for(engine: str, version: str) -> Support | None:
    engine = "postgres" if engine.startswith("postgres") else engine
    major = _major(engine, version)
    return next((s for s in EXTENDED_SUPPORT if s.engine == engine and s.major == major), None)


def vcpus(instance_class: str) -> int | None:
    return VCPUS.get(instance_class.rsplit(".", 1)[-1])


def _extended_billed(db: Session, org_id: uuid.UUID, rid: str, currency: str) -> float | None:
    """Monthly Extended Support charge straight from the bill, when it is itemised."""
    t = Base.metadata.tables["cost_line_items"]
    last = db.scalar(select(func.max(t.c.charge_date)).where(t.c.org_id == org_id))
    if last is None:
        return None
    amount = fx.converted(
        t.c.effective_cost, t.c.billing_currency, t.c.charge_date, currency, t.c.org_id
    )
    total = db.scalar(
        select(func.sum(amount)).where(
            t.c.org_id == org_id,
            t.c.resource_id == rid,
            t.c.usage_type.ilike("%ExtendedSupport%"),
            t.c.charge_date > last - timedelta(days=30),
        )
    )
    return float(total) * MONTH / 30 if total else None


def _extended_text(
    engine: str, s: Support, rate: float, year3: bool, multi_az: bool, basis: str
) -> str:
    when = f"{s.standard_ends:%d %b %Y}"
    extra = " (year 3 rate)" if year3 else ""
    both = ", for both Multi-AZ instances" if multi_az else ""
    return (
        f"Standard support for {engine} {s.major} ended on {when}; AWS now charges "
        f"${rate:.2f} per vCPU-hour{extra}{both}. Upgrading to a supported major version "
        f"removes the charge (cost from {basis})."
    )


@detector(
    "aws.rds_extended_support", category="database", title="Databases in paid Extended Support"
)
def rds_extended_support(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    today = utcnow().date()
    usd = fx.rate(db, today, "USD", currency, org_id=org_id)
    out = []
    for d in _resources(db, "rds_instance"):
        a = d.attributes or {}
        engine = a.get("engine") or ""
        version = str(a.get("engine_version") or "")
        if engine.startswith("aurora"):
            continue  # Aurora has its own schedule: covered by the AWS audit (4.9)
        s = support_for(engine, version)
        if s is None or today <= s.standard_ends:
            continue
        year3 = today > date(s.standard_ends.year + 2, s.standard_ends.month, 28)
        rate = RATE_YEAR3 if year3 else RATE_EARLY
        multi_az = bool(a.get("multi_az"))
        cpus = vcpus(a.get("instance_class") or "")
        billed = _extended_billed(db, org_id, d.resource_id, currency)
        if billed:
            saving, basis = billed, "billing"
        elif cpus and usd is not None:
            nodes = 2 if multi_az else 1
            saving, basis = cpus * nodes * rate * HOURS_MONTH * float(usd), "list price"
        else:
            continue
        ident = d.name or d.resource_id
        evidence: dict[str, Any] = {
            "engine": engine,
            "version": version,
            "basis": basis,
            "vcpus": cpus,
            "standard_support_ended": s.standard_ends.isoformat(),
            "rate_usd_vcpu_hour": rate,
            "multi_az": multi_az,
        }
        out.append(
            FindingIn(
                key=d.resource_id,
                title=(
                    f"Upgrade {ident} from {engine} {s.major} (paid Extended Support) "
                    f"to {s.upgrade_to}"
                ),
                description=_extended_text(engine, s, rate, year3, multi_az, basis),
                savings_monthly=round(saving, 2),
                currency=currency,
                risk="medium",
                effort="high",
                overlap=f"extended:{d.resource_id}",
                **_common(d),
                steps=[
                    "Check application and extension compatibility with the new major version.",
                    "Rehearse on a copy restored from a snapshot.",
                    "Use a blue/green deployment to switch over with about a minute of downtime.",
                ],
                commands=[
                    f"aws rds create-blue-green-deployment --region {d.region} "
                    f"--blue-green-deployment-name {ident}-upgrade --source {d.resource_id} "
                    f"--target-engine-version {s.upgrade_to}"
                ],
                evidence=evidence,
            )
        )
    return out


def _non_prod(r: CloudResource) -> str | None:
    tags = {k.lower(): str(v).lower() for k, v in (r.tags or {}).items()}
    for key in ("env", "environment", "stage", "tier"):
        if tags.get(key) in NON_PROD:
            return f"tag {key}={tags[key]}"
    name = (r.name or "").lower()
    for word in NON_PROD:
        if f"-{word}" in name or f"{word}-" in name or name.endswith(f"_{word}"):
            return f"name contains '{word}'"
    return None


@detector(
    "aws.rds_multi_az_nonprod", category="database", title="Multi-AZ on non-production databases"
)
def rds_multi_az_nonprod(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    dbs = [d for d in _resources(db, "rds_instance") if (d.attributes or {}).get("multi_az")]
    costs = resource_costs(db, org_id, [d.resource_id for d in dbs], days=30, currency=currency)
    out = []
    for d in dbs:
        why = _non_prod(d)
        if why is None or d.resource_id not in costs:
            continue
        monthly = costs[d.resource_id] * MONTH / 30
        ident = d.name or d.resource_id
        out.append(
            FindingIn(
                key=d.resource_id,
                title=f"Turn off Multi-AZ for non-production database {ident}",
                description=(
                    f"Looks non-production ({why}) but keeps a standby copy in a second zone, "
                    "which roughly "
                    "doubles instance and storage cost. Backups still protect the data."
                ),
                savings_monthly=round(monthly * 0.5, 2),
                currency=currency,
                risk="low",
                effort="low",
                overlap=d.resource_id,
                **_common(d),
                steps=[
                    "Confirm nobody relies on automatic failover for this environment.",
                    "Turn Multi-AZ off.",
                ],
                commands=[
                    f"aws rds modify-db-instance --region {d.region} "
                    f"--db-instance-identifier {ident} "
                    "--no-multi-az --apply-immediately"
                ],
                evidence={"reason": why, "monthly_cost": round(monthly, 2)},
            )
        )
    return out
