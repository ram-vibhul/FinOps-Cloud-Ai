"""Database detectors: Aurora I/O-Optimized, DynamoDB Standard-IA table class, idle DMS
replication instances and previous-generation ElastiCache nodes. Savings are computed
from the bill, split by usage type, with AWS's published price ratios."""

from __future__ import annotations

import re
import uuid

from sqlalchemy.orm import Session

from costlens.findings.aws import MONTH, _common, _resources
from costlens.findings.billing import usage_costs
from costlens.findings.registry import FindingIn, detector
from costlens.inventory.service import resource_costs

MIN_SAVING = 1.0
# Aurora I/O-Optimized: no I/O charges; instances +30%, storage 2.25x (0.10 -> 0.225/GB).
IOPT_INSTANCE = 0.30
IOPT_STORAGE = 1.25
# DynamoDB Standard-IA: storage 60% cheaper, reads/writes 25% dearer.
IA_STORAGE = 0.60
IA_THROUGHPUT = 0.25
PREVIOUS_CACHE = re.compile(r"^cache\.(t1|t2|m1|m2|m3|m4|r3|r4|c1)\.")
CACHE_GEN_SAVING = 0.10  # conservative: newer and Graviton nodes cost less per GiB


def aurora_class(usage: str | None) -> str:
    u = (usage or "").lower()
    if "storageiousage" in u or "storageio" in u:
        return "io"
    if "storageusage" in u:
        return "storage"
    if "instanceusage" in u or "serverlessv2" in u or "serverlessusage" in u:
        return "instance"
    return "other"


def dynamodb_class(usage: str | None) -> str:
    u = (usage or "").lower()
    if "timedstorage" in u:
        return "storage"
    if "capacityunit" in u or "requestunits" in u:
        return "throughput"
    return "other"


@detector("aws.aurora_io_optimized", category="database", title="Aurora clusters paying for I/O")
def aurora_io_optimized(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    clusters = [
        c
        for c in _resources(db, "rds_cluster")
        if str((c.attributes or {}).get("engine", "")).startswith("aurora")
        and (c.attributes or {}).get("storage_type") == "aurora"
    ]
    ids = [c.resource_id for c in clusters] + [
        m for c in clusters for m in (c.attributes or {}).get("member_arns") or []
    ]
    costs = usage_costs(db, org_id, currency, ids, aurora_class)
    out = []
    for c in clusters:
        mine = [c.resource_id, *((c.attributes or {}).get("member_arns") or [])]
        totals = {
            k: sum(costs.get(r, {}).get(k, 0.0) for r in mine)
            for k in ("io", "storage", "instance")
        }
        io, storage, instance = totals["io"], totals["storage"], totals["instance"]
        saving = round(io - IOPT_INSTANCE * instance - IOPT_STORAGE * storage, 2)
        if io <= 0 or saving < MIN_SAVING:
            continue
        share = io / max(io + storage + instance, 1e-9)
        out.append(
            FindingIn(
                key=c.resource_id,
                title=f"Switch Aurora cluster {c.name} to I/O-Optimized",
                description=(
                    f"I/O is {share:.0%} of the cluster's cost ({io:,.2f} {currency} a month). "
                    "I/O-Optimized drops I/O charges for 30% more on instances and 2.25 times the "
                    "storage price; with this mix it is cheaper."
                ),
                savings_monthly=saving,
                currency=currency,
                risk="low",
                effort="low",
                overlap=c.resource_id,
                **_common(c),
                steps=[
                    "Change the cluster storage configuration (no downtime; you can switch "
                    "back once every 30 days).",
                ],
                commands=[
                    f"aws rds modify-db-cluster --region {c.region} --db-cluster-identifier "
                    f"{c.name} --storage-type aurora-iopt1 --apply-immediately"
                ],
                evidence={k: round(v, 2) for k, v in totals.items()},
            )
        )
    return out


@detector("aws.dynamodb_table_class", category="database", title="DynamoDB tables for Standard-IA")
def dynamodb_table_class(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    tables = [
        t
        for t in _resources(db, "dynamodb_table")
        if (t.attributes or {}).get("table_class") == "STANDARD"
    ]
    costs = usage_costs(db, org_id, currency, [t.resource_id for t in tables], dynamodb_class)
    out = []
    for t in tables:
        c = costs.get(t.resource_id, {})
        storage, throughput = c.get("storage", 0.0), c.get("throughput", 0.0)
        saving = round(storage * IA_STORAGE - throughput * IA_THROUGHPUT, 2)
        if saving < MIN_SAVING:
            continue
        out.append(
            FindingIn(
                key=t.resource_id,
                title=f"Move DynamoDB table {t.name} to Standard-IA",
                description=(
                    f"Storage costs {storage:,.2f} {currency} a month against {throughput:,.2f} for "
                    "reads and writes. Standard-IA stores data 60% cheaper and charges 25% more "
                    "for reads and writes; for this table that nets the saving shown."
                ),
                savings_monthly=saving,
                currency=currency,
                risk="low",
                effort="low",
                overlap=t.resource_id,
                **_common(t),
                steps=["Change the table class (online; at most twice in 30 days)."],
                commands=[
                    f"aws dynamodb update-table --region {t.region} --table-name {t.name} "
                    "--table-class STANDARD_INFREQUENT_ACCESS"
                ],
                evidence={"storage": round(storage, 2), "throughput": round(throughput, 2)},
            )
        )
    return out


@detector("aws.dms_idle", category="waste", title="DMS replication instances with no running tasks")
def dms_idle(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    idle = [
        r
        for r in _resources(db, "dms_replication_instance")
        if (r.attributes or {}).get("running_tasks", 0) == 0 and r.state == "available"
    ]
    cost = resource_costs(db, org_id, [r.resource_id for r in idle], days=30, currency=currency)
    out = []
    for r in idle:
        monthly = cost.get(r.resource_id, 0.0) * MONTH / 30
        if monthly < MIN_SAVING:
            continue
        tasks = (r.attributes or {}).get("tasks", 0)
        out.append(
            FindingIn(
                key=r.resource_id,
                title=f"Delete idle DMS replication instance {r.name}",
                description=(
                    f"It has {tasks} task(s) and none is running: the migration looks finished. "
                    "The instance is billed every hour regardless."
                ),
                savings_monthly=round(monthly, 2),
                currency=currency,
                risk="low",
                effort="low",
                overlap=r.resource_id,
                **_common(r),
                steps=[
                    "Confirm the migration is finished.",
                    "Delete the tasks, then the instance.",
                ],
                commands=[
                    f"aws dms delete-replication-instance --region {r.region} "
                    f"--replication-instance-arn {r.resource_id}"
                ],
                evidence={"tasks": tasks, "monthly_cost": round(monthly, 2)},
            )
        )
    return out


@detector(
    "aws.elasticache_previous_gen", category="database", title="Previous-generation cache nodes"
)
def elasticache_previous_gen(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    old = [
        c
        for c in _resources(db, "elasticache_cluster")
        if PREVIOUS_CACHE.match(str((c.attributes or {}).get("node_type") or ""))
    ]
    cost = resource_costs(db, org_id, [c.resource_id for c in old], days=30, currency=currency)
    out = []
    for c in old:
        monthly = cost.get(c.resource_id, 0.0) * MONTH / 30
        saving = round(monthly * CACHE_GEN_SAVING, 2)
        if saving < MIN_SAVING:
            continue
        node = str((c.attributes or {}).get("node_type"))
        size = node.split(".", 2)[-1]
        target = f"cache.{'t4g' if node.startswith('cache.t') else 'r7g' if '.r' in node else 'm7g'}.{size}"
        out.append(
            FindingIn(
                key=c.resource_id,
                title=f"Move cache {c.name} from {node} to {target}",
                description=(
                    "Previous-generation nodes cost more per GiB than current Graviton nodes, "
                    "which are also faster. 10% is a conservative estimate."
                ),
                savings_monthly=saving,
                currency=currency,
                risk="medium",
                effort="medium",
                overlap=c.resource_id,
                **_common(c),
                steps=[
                    "Test the new node type on a copy.",
                    "Modify the node type in a maintenance window.",
                ],
                commands=[
                    f"aws elasticache modify-replication-group --region {c.region} "
                    f"--replication-group-id {(c.attributes or {}).get('replication_group') or c.name} "
                    f"--cache-node-type {target} --apply-immediately"
                ],
                evidence={"node_type": node, "monthly_cost": round(monthly, 2)},
            )
        )
    return out
