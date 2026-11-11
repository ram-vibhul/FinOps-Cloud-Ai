"""Database and cache advice that helps performance as well as cost:
RDS Provisioned IOPS (io1) storage moved to gp3, and over-sized ElastiCache nodes."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from ccradar.findings.aws import MONTH, _common, _resources
from ccradar.findings.aws_compute import _usd
from ccradar.findings.aws_usage import HALF, MIN_DAYS, _usage
from ccradar.findings.registry import FindingIn, detector
from ccradar.inventory.service import resource_costs

MIN_SAVING = 1.0
# RDS storage list prices, USD per month (us-east-1, Single-AZ; Multi-AZ doubles both).
IO1_GB, IO1_IOPS = 0.125, 0.10
GP3_GB, GP3_IOPS = 0.115, 0.02
GP3_BASELINE_SMALL, GP3_BASELINE_LARGE, GP3_LARGE_GB = 3000, 12000, 400
CACHE_MEMORY_MAX, CACHE_CPU_MAX = 35.0, 40.0


def gp3_iops_cost(gb: float, iops: float) -> float:
    baseline = GP3_BASELINE_LARGE if gb >= GP3_LARGE_GB else GP3_BASELINE_SMALL
    return max(iops - baseline, 0) * GP3_IOPS


@detector(
    "aws.rds_io1_to_gp3", category="database", title="RDS Provisioned IOPS storage cheaper as gp3"
)
def rds_io1_to_gp3(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    usd = _usd(db, org_id, currency)
    if usd is None:
        return []
    dbs = [
        d
        for d in _resources(db, "rds_instance")
        if (d.attributes or {}).get("storage_type") == "io1"
    ]
    usage = _usage(db, [d.resource_id for d in dbs])
    out = []
    for d in dbs:
        a = d.attributes or {}
        gb, iops = float(a.get("storage_gb") or 0), float(a.get("iops") or 0)
        if not gb or not iops:
            continue
        u = usage.get(d.resource_id, {})
        peak = sum((u.get(k) or {}).get("max", 0.0) for k in ("read_iops", "write_iops"))
        needed = min(iops, max(peak * 1.3, 0)) if peak else iops  # keep 30% headroom over the peak
        copies = 2 if a.get("multi_az") else 1
        io1 = (gb * IO1_GB + iops * IO1_IOPS) * copies
        gp3 = (gb * GP3_GB + gp3_iops_cost(gb, needed)) * copies
        saving = round((io1 - gp3) * usd, 2)
        if saving < MIN_SAVING:
            continue
        ident = d.name or d.resource_id
        out.append(
            FindingIn(
                key=d.resource_id,
                title=f"Move database {ident} storage from io1 to gp3",
                description=(
                    f"{gb:,.0f} GB of io1 with {iops:,.0f} provisioned IOPS"
                    + (f"; the busiest hour used {peak:,.0f} IOPS" if peak else "")
                    + f". gp3 gives {needed:,.0f} IOPS for much less, and its baseline "
                    f"({GP3_BASELINE_LARGE if gb >= GP3_LARGE_GB else GP3_BASELINE_SMALL:,} IOPS) "
                    "is included (list prices)."
                ),
                savings_monthly=saving,
                currency=currency,
                risk="low",
                effort="low",
                overlap=f"storage:{d.resource_id}",
                **_common(d),
                steps=[
                    "Change the storage type online (performance stays available during the change)."
                ],
                commands=[
                    f"aws rds modify-db-instance --region {d.region} --db-instance-identifier {ident} "
                    f"--storage-type gp3 --iops {int(max(needed, 3000))} --apply-immediately"
                ],
                evidence={
                    "storage_gb": gb,
                    "iops": iops,
                    "peak_iops": round(peak),
                    "gp3_iops": round(needed),
                    "basis": "list price",
                },
            )
        )
    return out


def smaller_node(node: str) -> str | None:
    family, _, size = node.removeprefix("cache.").partition(".")
    nxt = HALF.get(size)
    if not nxt or (nxt in ("medium", "small", "micro") and not family.startswith("t")):
        return None
    return f"cache.{family}.{nxt}"


@detector("aws.elasticache_rightsizing", category="database", title="Over-sized cache nodes")
def elasticache_rightsizing(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    caches = [c for c in _resources(db, "elasticache_cluster") if c.state == "available"]
    ids = [c.resource_id for c in caches]
    usage, cost = _usage(db, ids), resource_costs(db, org_id, ids, days=30, currency=currency)
    out = []
    for c in caches:
        u = usage.get(c.resource_id, {})
        mem, cpu, ev = u.get("cache_memory_pct"), u.get("engine_cpu"), u.get("evictions")
        node = str((c.attributes or {}).get("node_type") or "")
        target = smaller_node(node)
        monthly = cost.get(c.resource_id, 0.0) * MONTH / 30
        if not (mem and cpu and target) or mem["days"] < MIN_DAYS or monthly < MIN_SAVING:
            continue
        if mem["max"] >= CACHE_MEMORY_MAX or cpu["max"] >= CACHE_CPU_MAX or (ev and ev["max"] > 0):
            continue
        out.append(
            FindingIn(
                key=c.resource_id,
                title=f"Move cache {c.name} from {node} to {target}",
                description=(
                    f"Memory use peaked at {mem['max']:.0f}% and engine CPU at {cpu['max']:.0f}% in "
                    f"{mem['days']} days with no evictions; half the memory still leaves room."
                ),
                savings_monthly=round(monthly * 0.5, 2),
                currency=currency,
                risk="medium",
                effort="medium",
                overlap=c.resource_id,
                **_common(c),
                steps=[
                    "Change the node type in a maintenance window (online for Redis/Valkey replication groups)."
                ],
                commands=[
                    f"aws elasticache modify-replication-group --region {c.region} --replication-group-id "
                    f"{(c.attributes or {}).get('replication_group') or c.name} --cache-node-type {target} "
                    "--apply-immediately"
                ],
                evidence={
                    "days": mem["days"],
                    "memory_max_pct": round(mem["max"], 1),
                    "cpu_max": round(cpu["max"], 1),
                    "monthly_cost": round(monthly, 2),
                    "target": target,
                },
            )
        )
    return out
