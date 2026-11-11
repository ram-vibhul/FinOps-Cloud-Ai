"""AWS waste detectors over the resource inventory and the bill.

Savings use the resource's real cost from the bill (last 30 days, scaled to a month)
when the bill has resource IDs (CUR), else public list prices (us-east-1, USD,
converted) and say so. Nothing is deleted by CloudCostRadar: findings carry safe steps."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ccradar.costs import fx
from ccradar.db.base import Base, utcnow
from ccradar.findings.registry import FindingIn, detector
from ccradar.inventory.service import resource_costs
from ccradar.models import CloudResource

MONTH = 30.4
# List prices, USD per GB-month (us-east-1); used only when the bill has no figure.
EBS_GB_MONTH = {
    "gp2": 0.10,
    "gp3": 0.08,
    "io1": 0.125,
    "io2": 0.125,
    "st1": 0.045,
    "sc1": 0.015,
    "standard": 0.05,
}
SNAPSHOT_GB_MONTH = 0.05
EIP_HOUR = 0.005
PREVIOUS_GEN = re.compile(r"^(t2|m3|m4|c3|c4|r3|r4|i2|d2|x1|p2|g3)\.")
SUCCESSOR = {
    "t2": "t3",
    "m3": "m6i",
    "m4": "m6i",
    "c3": "c6i",
    "c4": "c6i",
    "r3": "r6i",
    "r4": "r6i",
    "i2": "i4i",
    "d2": "d3",
    "x1": "x2iedn",
    "p2": "p3",
    "g3": "g4dn",
}


def _aware(dt: datetime | None) -> datetime | None:
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=UTC)


class Pricing:
    """Bill first, list price second (converted to the organisation's currency)."""

    def __init__(self, db: Session, org_id: uuid.UUID, currency: str, ids: list[str]) -> None:
        self.currency = currency
        billed = resource_costs(db, org_id, ids, days=30, currency=currency)
        self.billed = {k: v * MONTH / 30 for k, v in billed.items()}
        rate = fx.rate(db, utcnow().date(), "USD", currency, org_id=org_id)
        self.usd = float(rate) if rate is not None else None

    def cost(self, resource_id: str, list_usd: float) -> tuple[float, str] | None:
        if resource_id in self.billed:
            return round(self.billed[resource_id], 2), "billing"
        if self.usd is None:
            return None  # no bill line and no exchange rate: cannot say
        return round(list_usd * self.usd, 2), "list price"


def _resources(db: Session, *types: str) -> list[CloudResource]:
    return list(
        db.scalars(
            select(CloudResource).where(
                CloudResource.provider == "aws",
                CloudResource.resource_type.in_(types),
                CloudResource.state != "deleted",
            )
        )
    )


def _common(r: CloudResource) -> dict[str, Any]:
    return {
        "provider": "aws",
        "account_id": r.account_id,
        "region": r.region,
        "service": r.service,
        "resource_type": r.resource_type,
        "resource_id": r.resource_id,
    }


def _label(r: CloudResource) -> str:
    return f"{r.name} ({r.resource_id})" if r.name and r.name != r.resource_id else r.resource_id


@detector("aws.ebs_unattached", category="waste", title="Unattached EBS volumes")
def ebs_unattached(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    vols = [r for r in _resources(db, "ebs_volume") if r.state == "available"]
    pricing = Pricing(db, org_id, currency, [v.resource_id for v in vols])
    out = []
    for v in vols:
        size = float((v.attributes or {}).get("size_gb") or 0)
        vtype = (v.attributes or {}).get("volume_type") or "gp2"
        priced = pricing.cost(v.resource_id, size * EBS_GB_MONTH.get(vtype, 0.10))
        if priced is None or priced[0] < 1:
            continue
        saving, basis = priced
        out.append(
            FindingIn(
                key=v.resource_id,
                title=f"Delete unattached volume {_label(v)}",
                description=(
                    f"{size:.0f} GB {vtype} volume attached to nothing (cost from {basis})."
                ),
                savings_monthly=saving,
                currency=currency,
                risk="medium",
                effort="low",
                **_common(v),
                steps=[
                    "Check nobody needs it (owner tag, recent detach).",
                    "Take a snapshot first, so it can be restored.",
                    "Delete the volume.",
                ],
                commands=[
                    f"aws ec2 create-snapshot --region {v.region} --volume-id {v.resource_id} "
                    f"--description 'before delete (CloudCostRadar)'",
                    f"aws ec2 delete-volume --region {v.region} --volume-id {v.resource_id}",
                ],
                evidence={"size_gb": size, "volume_type": vtype, "basis": basis},
            )
        )
    return out


@detector("aws.eip_idle", category="waste", title="Idle Elastic IP addresses")
def eip_idle(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    ips = [r for r in _resources(db, "elastic_ip") if r.state == "unattached"]
    pricing = Pricing(db, org_id, currency, [i.resource_id for i in ips])
    out = []
    for ip in ips:
        priced = pricing.cost(ip.resource_id, EIP_HOUR * 24 * MONTH)
        if priced is None:
            continue
        out.append(
            FindingIn(
                key=ip.resource_id,
                title=f"Release idle Elastic IP {_label(ip)}",
                description=(
                    "Public IPv4 addresses are charged by the hour even when attached to nothing."
                ),
                savings_monthly=priced[0],
                currency=currency,
                risk="low",
                effort="low",
                **_common(ip),
                steps=[
                    "Make sure no DNS record or allow-list still uses this address.",
                    "Release it.",
                ],
                commands=[
                    f"aws ec2 release-address --region {ip.region} --allocation-id {ip.resource_id}"
                ],
                evidence={"public_ip": (ip.attributes or {}).get("public_ip"), "basis": priced[1]},
            )
        )
    return out


@detector("aws.snapshot_old", category="waste", title="Old snapshots of deleted volumes")
def snapshot_old(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    cutoff = utcnow() - timedelta(days=90)
    live_volumes = {v.resource_id for v in _resources(db, "ebs_volume")}
    snaps = [
        s
        for s in _resources(db, "ebs_snapshot")
        if (_aware(s.created_in_provider_at) or utcnow()) < cutoff
        and (s.attributes or {}).get("volume") not in live_volumes
    ]
    pricing = Pricing(db, org_id, currency, [s.resource_id for s in snaps])
    out = []
    for s in snaps:
        size = float((s.attributes or {}).get("size_gb") or 0)
        priced = pricing.cost(s.resource_id, size * SNAPSHOT_GB_MONTH)
        if priced is None or priced[0] < 1:
            continue
        age = (utcnow() - (_aware(s.created_in_provider_at) or utcnow())).days
        out.append(
            FindingIn(
                key=s.resource_id,
                title=f"Delete old snapshot {_label(s)}",
                description=(
                    f"{age} days old and its volume no longer exists. Snapshots are incremental, "
                    f"so the list-price figure is an upper bound (cost from {priced[1]})."
                ),
                savings_monthly=priced[0],
                currency=currency,
                risk="medium",
                effort="low",
                **_common(s),
                steps=[
                    "Check it is not needed for compliance or an AMI.",
                    "Delete it (or archive it).",
                ],
                commands=[
                    f"aws ec2 delete-snapshot --region {s.region} --snapshot-id {s.resource_id}"
                ],
                evidence={"age_days": age, "size_gb": size, "basis": priced[1]},
            )
        )
    return out


@detector("aws.ec2_stopped", category="waste", title="Instances stopped for a long time")
def ec2_stopped(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    cutoff = utcnow() - timedelta(days=14)
    stopped = [
        i
        for i in _resources(db, "ec2_instance")
        if i.state == "stopped" and (_aware(i.first_seen_at) or utcnow()) < cutoff
    ]
    volumes = _resources(db, "ebs_volume")
    out = []
    for inst in stopped:
        attached = [
            v
            for v in volumes
            if inst.resource_id in ((v.attributes or {}).get("attached_to") or [])
        ]
        pricing = Pricing(db, org_id, currency, [v.resource_id for v in attached])
        total = 0.0
        for v in attached:
            size = float((v.attributes or {}).get("size_gb") or 0)
            priced = pricing.cost(
                v.resource_id,
                size * EBS_GB_MONTH.get((v.attributes or {}).get("volume_type") or "gp2", 0.10),
            )
            total += priced[0] if priced else 0
        if total < 1:
            continue
        out.append(
            FindingIn(
                key=inst.resource_id,
                title=f"Remove long-stopped instance {_label(inst)}",
                description=(
                    "Stopped instances do not pay for compute but keep paying for their "
                    f"{len(attached)} volume(s)."
                ),
                savings_monthly=round(total, 2),
                currency=currency,
                risk="medium",
                effort="medium",
                **_common(inst),
                steps=[
                    "Ask the owner if it is still needed.",
                    "Create an AMI (keeps a restorable copy).",
                    "Terminate the instance and delete its volumes.",
                ],
                commands=[
                    f"aws ec2 create-image --region {inst.region} --instance-id {inst.resource_id} "
                    f"--name ccradar-{inst.resource_id}",
                    f"aws ec2 terminate-instances --region {inst.region} "
                    f"--instance-ids {inst.resource_id}",
                ],
                evidence={"volumes": [v.resource_id for v in attached]},
            )
        )
    return out


def _usage_types(db: Session, org_id: uuid.UUID, resource_id: str, days: int = 14) -> set[str]:
    t = Base.metadata.tables["cost_line_items"]
    last = db.scalar(select(func.max(t.c.charge_date)).where(t.c.org_id == org_id))
    if last is None:
        return set()
    rows = db.execute(
        select(t.c.usage_type)
        .distinct()
        .where(
            t.c.org_id == org_id,
            t.c.resource_id == resource_id,
            t.c.charge_date > last - timedelta(days=days),
        )
    ).scalars()
    return {u for u in rows if u}


def _idle_by_usage(
    db: Session,
    org_id: uuid.UUID,
    currency: str,
    rtype: str,
    busy: str,
    name: str,
    why: str,
    command: str,
) -> list[FindingIn]:
    """Billed for hours but no traffic usage type in 14 days (needs the CUR billing export)."""
    resources = _resources(db, rtype)
    pricing = Pricing(db, org_id, currency, [r.resource_id for r in resources])
    out = []
    for r in resources:
        usage = _usage_types(db, org_id, r.resource_id)
        if not usage or any(busy in u for u in usage) or r.resource_id not in pricing.billed:
            continue  # no bill detail (cannot tell) or it has traffic
        out.append(
            FindingIn(
                key=r.resource_id,
                title=f"{name} {_label(r)}",
                description=why,
                savings_monthly=round(pricing.billed[r.resource_id], 2),
                currency=currency,
                risk="medium",
                effort="low",
                **_common(r),
                steps=["Confirm nothing depends on it (DNS, route tables, peers).", "Delete it."],
                commands=[command.format(region=r.region, id=r.resource_id)],
                evidence={"usage_types": sorted(usage)},
            )
        )
    return out


@detector("aws.lb_unused", category="waste", title="Load balancers without traffic")
def lb_unused(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    return _idle_by_usage(
        db,
        org_id,
        currency,
        "load_balancer",
        "LCU",
        "Delete unused load balancer",
        "Billed for its hours with no capacity-unit (traffic) usage in the last 14 days.",
        "aws elbv2 delete-load-balancer --region {region} --load-balancer-arn {id}",
    )


@detector("aws.nat_idle", category="waste", title="NAT gateways without traffic")
def nat_idle(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    return _idle_by_usage(
        db,
        org_id,
        currency,
        "nat_gateway",
        "Bytes",
        "Delete idle NAT gateway",
        "Billed for its hours but processed no data in the last 14 days.",
        "aws ec2 delete-nat-gateway --region {region} --nat-gateway-id {id}",
    )


@detector("aws.previous_generation", category="rightsizing", title="Previous-generation resources")
def previous_generation(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    out = []
    gp2 = [
        v
        for v in _resources(db, "ebs_volume")
        if (v.attributes or {}).get("volume_type") == "gp2" and v.state == "in-use"
    ]
    pricing = Pricing(db, org_id, currency, [v.resource_id for v in gp2])
    for v in gp2:
        size = float((v.attributes or {}).get("size_gb") or 0)
        priced = pricing.cost(v.resource_id, size * EBS_GB_MONTH["gp2"])
        if priced is None or priced[0] * 0.2 < 1:
            continue
        out.append(
            FindingIn(
                key=f"gp3:{v.resource_id}",
                title=f"Move volume {_label(v)} from gp2 to gp3",
                description=(
                    "gp3 costs 20% less per GB than gp2, with the same or better baseline "
                    "performance."
                ),
                savings_monthly=round(priced[0] * 0.2, 2),
                currency=currency,
                risk="low",
                effort="low",
                overlap=v.resource_id,
                **_common(v),
                steps=["Change the volume type online (no downtime)."],
                commands=[
                    f"aws ec2 modify-volume --region {v.region} --volume-id {v.resource_id} "
                    "--volume-type gp3"
                ],
                evidence={"size_gb": size, "basis": priced[1]},
            )
        )
    instances = [
        i
        for i in _resources(db, "ec2_instance")
        if i.state == "running"
        and PREVIOUS_GEN.match((i.attributes or {}).get("instance_type") or "")
    ]
    billed = Pricing(db, org_id, currency, [i.resource_id for i in instances]).billed
    for i in instances:
        itype = (i.attributes or {}).get("instance_type") or ""
        family, size = itype.split(".", 1)
        cost = billed.get(i.resource_id)
        if not cost or cost * 0.1 < 1:
            continue
        new = f"{SUCCESSOR.get(family, family)}.{size}"
        out.append(
            FindingIn(
                key=f"gen:{i.resource_id}",
                title=f"Move {_label(i)} from {itype} to {new}",
                description=(
                    "Newer generations give more performance per dollar; about 10% saving is a "
                    "conservative estimate. Check the AMI supports the new family (Nitro/ENA)."
                ),
                savings_monthly=round(cost * 0.1, 2),
                currency=currency,
                risk="medium",
                effort="medium",
                overlap=i.resource_id,
                **_common(i),
                steps=[
                    "Test the workload on the new type.",
                    "Stop, change the type, start (short downtime).",
                ],
                commands=[
                    f"aws ec2 modify-instance-attribute --region {i.region} "
                    f"--instance-id {i.resource_id} "
                    f"--instance-type {new}"
                ],
                evidence={"instance_type": itype, "monthly_cost": round(cost, 2)},
            )
        )
    return out
