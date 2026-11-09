"""Compute and container detectors: Lambda on Arm, Spot for non-production capacity,
idle Auto Scaling groups and EKS node groups, EKS extended support, ECR clean-up.

Savings come from the bill (resource-level costs) wherever possible; list prices are
used only for EKS extended support and ECR storage, and say so."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from costlens.costs import fx
from costlens.db.base import utcnow
from costlens.findings.aws import MONTH, _common, _resources
from costlens.findings.aws_db import _extended_billed, _non_prod
from costlens.findings.aws_usage import IDLE_CPU, MIN_DAYS, _usage
from costlens.findings.registry import FindingIn, detector
from costlens.inventory.service import resource_costs
from costlens.models import CloudResource

MIN_SAVING = 1.0
ARM_SAVING = 0.20  # Lambda on arm64 costs 20% less per GB-second
SPOT_SAVING = 0.60  # conservative: Spot is usually 60-90% below On-Demand
ECR_GB_MONTH_USD = 0.10
EKS_EXTENDED_EXTRA_USD_HOUR = 0.50  # 0.60 in extended support vs 0.10 in standard
# Kubernetes minor version -> end of EKS standard support (extended support follows for
# 12 months). Source: the EKS Kubernetes release calendar; check it when versions change.
EKS_STANDARD_END = {
    "1.23": date(2024, 10, 11),
    "1.24": date(2025, 1, 31),
    "1.25": date(2025, 5, 1),
    "1.26": date(2025, 6, 11),
    "1.27": date(2025, 7, 24),
    "1.28": date(2025, 11, 26),
    "1.29": date(2026, 3, 23),
    "1.30": date(2026, 7, 23),
    "1.31": date(2026, 11, 26),
    "1.32": date(2027, 3, 23),
    "1.33": date(2027, 7, 29),
}
NO_ARM = {"go1.x", "java8", "python3.6", "python3.7", "nodejs10.x", "dotnetcore3.1", "ruby2.5"}


def _monthly(db: Session, org_id: uuid.UUID, currency: str, ids: list[str]) -> dict[str, float]:
    costs = resource_costs(db, org_id, ids, days=30, currency=currency)
    return {k: v * MONTH / 30 for k, v in costs.items()}


def _usd(db: Session, org_id: uuid.UUID, currency: str) -> float | None:
    rate = fx.rate(db, utcnow().date(), "USD", currency, org_id=org_id)
    return float(rate) if rate is not None else None


@detector("aws.lambda_arm64", category="rightsizing", title="Lambda functions not on Arm")
def lambda_arm64(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    fns = [
        f
        for f in _resources(db, "lambda_function")
        if "arm64" not in ((f.attributes or {}).get("architectures") or ["x86_64"])
        and (f.attributes or {}).get("runtime") not in NO_ARM
    ]
    cost = _monthly(db, org_id, currency, [f.resource_id for f in fns])
    out = []
    for f in fns:
        monthly = cost.get(f.resource_id, 0.0)
        saving = round(monthly * ARM_SAVING, 2)
        if saving < MIN_SAVING:
            continue
        a = f.attributes or {}
        runtime, image = a.get("runtime") or "", a.get("package_type") == "Image"
        rebuild = image or runtime.startswith("provided")
        out.append(
            FindingIn(
                key=f.resource_id,
                title=f"Run Lambda function {f.name} on Arm (Graviton)",
                description=(
                    f"It runs on x86_64 ({runtime or 'container image'}). Arm costs 20% less "
                    "for the same memory and is often faster."
                    + (
                        " The code must be rebuilt for arm64."
                        if rebuild
                        else " Check native dependencies (compiled packages) first."
                    )
                ),
                savings_monthly=saving,
                currency=currency,
                risk="low",
                effort="medium" if rebuild else "low",
                overlap=f.resource_id,
                **_common(f),
                steps=[
                    "Publish a version built for arm64 and test it behind an alias.",
                    "Switch the alias, then watch errors and duration for a day.",
                ],
                commands=[
                    f"aws lambda update-function-code --region {f.region} --function-name "
                    f"{f.name} --architectures arm64 --zip-file fileb://function.zip"
                ],
                evidence={"runtime": runtime, "monthly_cost": round(monthly, 2), **a},
            )
        )
    return out


def _nodes(instances: list[CloudResource], cluster: str, group: str) -> list[CloudResource]:
    return [
        i
        for i in instances
        if (i.tags or {}).get("eks:cluster-name") == cluster
        and (i.tags or {}).get("eks:nodegroup-name") == group
    ]


@detector(
    "aws.spot_nonprod_capacity", category="rightsizing", title="Spot for non-production capacity"
)
def spot_nonprod_capacity(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    instances = [i for i in _resources(db, "ec2_instance") if i.state == "running"]
    by_id = {i.resource_id: i for i in instances}
    clusters = {c.name: c for c in _resources(db, "eks_cluster")}
    groups: list[tuple[CloudResource, list[CloudResource], str, str]] = []
    for g in _resources(db, "autoscaling_group"):
        a = g.attributes or {}
        if a.get("eks_nodegroup") or (a.get("on_demand_pct") or 0) < 100:
            continue  # EKS groups are checked as node groups; Spot already in use
        why = _non_prod(g)
        members = [by_id[i] for i in a.get("instance_ids") or [] if i in by_id]
        if why and members:
            groups.append((g, members, why, "Auto Scaling group"))
    for ng in _resources(db, "eks_nodegroup"):
        a = ng.attributes or {}
        if a.get("capacity_type") != "ON_DEMAND":
            continue
        cluster = clusters.get(a.get("cluster") or "")
        why = _non_prod(ng) or (_non_prod(cluster) if cluster else None)
        members = _nodes(instances, a.get("cluster") or "", ng.name or "")
        if why and members:
            groups.append((ng, members, why, "EKS node group"))
    cost = _monthly(db, org_id, currency, [m.resource_id for _, ms, _, _ in groups for m in ms])
    out = []
    for g, members, why, kind in groups:
        monthly = sum(cost.get(m.resource_id, 0.0) for m in members)
        saving = round(monthly * SPOT_SAVING, 2)
        if saving < MIN_SAVING:
            continue
        eks = kind == "EKS node group"
        out.append(
            FindingIn(
                key=g.resource_id,
                title=f"Use Spot for non-production {kind.lower()} {g.name}",
                description=(
                    f"Looks non-production ({why}) and runs {len(members)} On-Demand "
                    f"instance(s). Spot is usually 60 to 90% cheaper; the estimate uses 60%. "
                    "Spot capacity can be reclaimed with two minutes' notice, so use several "
                    "instance types."
                ),
                savings_monthly=saving,
                currency=currency,
                risk="medium",
                effort="medium" if eks else "low",
                overlap=g.resource_id,
                **_common(g),
                steps=(
                    [
                        "Create a new node group with capacity type SPOT and 3+ instance types.",
                        "Cordon and drain the old node group, then delete it.",
                    ]
                    if eks
                    else [
                        "Add a mixed instances policy with 3+ instance types and 0% On-Demand "
                        "above base capacity.",
                        "Start an instance refresh.",
                    ]
                ),
                commands=(
                    [
                        f"aws eks create-nodegroup --region {g.region} --cluster-name "
                        f"{(g.attributes or {}).get('cluster')} --nodegroup-name {g.name}-spot "
                        "--capacity-type SPOT --instance-types m6i.large m5.large m6a.large "
                        "--subnets <subnets> --node-role <node-role-arn>"
                    ]
                    if eks
                    else [
                        f"aws autoscaling start-instance-refresh --region {g.region} "
                        f"--auto-scaling-group-name {g.name}"
                    ]
                ),
                evidence={
                    "reason": why,
                    "instances": len(members),
                    "monthly_cost": round(monthly, 2),
                    "spot_saving_assumed_pct": int(SPOT_SAVING * 100),
                },
            )
        )
    return out


def _idle_groups(db: Session, org_id: uuid.UUID, currency: str, *, eks: bool) -> list[FindingIn]:
    instances = {i.resource_id: i for i in _resources(db, "ec2_instance") if i.state == "running"}
    groups = [
        g
        for g in _resources(db, "autoscaling_group")
        if bool((g.attributes or {}).get("eks_nodegroup")) == eks
        and ((g.attributes or {}).get("desired") or 0) > 0
    ]
    members = {
        g.resource_id: [i for i in (g.attributes or {}).get("instance_ids") or [] if i in instances]
        for g in groups
    }
    ids = [i for ms in members.values() for i in ms]
    usage, cost = _usage(db, ids), _monthly(db, org_id, currency, ids)
    out = []
    for g in groups:
        ms = members[g.resource_id]
        cpus = [usage.get(i, {}).get("cpu_pct") for i in ms]
        if not ms or any(c is None or c["days"] < MIN_DAYS for c in cpus):
            continue  # every instance needs a week of data
        peak = max(c["max"] for c in cpus if c)
        monthly = sum(cost.get(i, 0.0) for i in ms)
        if peak >= IDLE_CPU or monthly < MIN_SAVING:
            continue
        a = g.attributes or {}
        days = min(c["days"] for c in cpus if c)
        label = (
            f"EKS node group {a.get('eks_nodegroup')}" if eks else f"Auto Scaling group {g.name}"
        )
        command = (
            f"aws eks update-nodegroup-config --region {g.region} --cluster-name "
            f"{a.get('eks_cluster')} --nodegroup-name {a.get('eks_nodegroup')} "
            f"--scaling-config minSize=0,maxSize={a.get('max') or 1},desiredSize=0"
            if eks
            else f"aws autoscaling update-auto-scaling-group --region {g.region} "
            f"--auto-scaling-group-name {g.name} --min-size 0 --desired-capacity 0"
        )
        out.append(
            FindingIn(
                key=g.resource_id,
                title=f"Scale down idle {label}",
                description=(
                    f"Its {len(ms)} instance(s) never went above {peak:.1f}% CPU in {days} days."
                    + (
                        " If nothing is scheduled on these nodes, scale the group to zero or "
                        "move its workloads to another node group."
                        if eks
                        else " Scale it to zero, or delete it if nothing uses it."
                    )
                ),
                savings_monthly=round(monthly, 2),
                currency=currency,
                risk="medium",
                effort="low",
                overlap=g.resource_id,
                **_common(g),
                steps=[
                    "Confirm with the owner that the group is not a standby for traffic peaks.",
                    "Scale it to zero (minimum and desired); delete it after a quiet week.",
                ],
                commands=[command],
                evidence={
                    "instances": len(ms),
                    "cpu_max": round(peak, 1),
                    "days": days,
                    "monthly_cost": round(monthly, 2),
                    "desired": a.get("desired"),
                    "min": a.get("min"),
                },
            )
        )
    return out


@detector("aws.idle_asgs", category="waste", title="Idle Auto Scaling groups")
def idle_asgs(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    return _idle_groups(db, org_id, currency, eks=False)


@detector("aws.eks_idle_nodegroups", category="waste", title="Idle EKS node groups")
def eks_idle_nodegroups(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    return _idle_groups(db, org_id, currency, eks=True)


def eks_support(version: str, today: date) -> str:
    """standard | extended | unknown (a version newer than the calendar here)."""
    end = EKS_STANDARD_END.get(version)
    if end is None:
        try:
            older = tuple(int(x) for x in version.split(".")[:2]) < (1, 23)
        except ValueError:
            return "unknown"
        return "extended" if older else "unknown"
    return "extended" if today > end else "standard"


@detector(
    "aws.eks_extended_support", category="containers", title="EKS clusters in extended support"
)
def eks_extended_support(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    today = utcnow().date()
    usd = _usd(db, org_id, currency)
    newest = max(EKS_STANDARD_END, key=lambda v: tuple(int(x) for x in v.split(".")))
    out = []
    for c in _resources(db, "eks_cluster"):
        a = c.attributes or {}
        version = str(a.get("version") or "")
        if eks_support(version, today) != "extended":
            continue
        billed = _extended_billed(db, org_id, c.resource_id, currency)
        if billed is not None:
            monthly, basis = billed, "billing"
        elif usd is not None:
            monthly, basis = EKS_EXTENDED_EXTRA_USD_HOUR * 24 * MONTH * usd, "list price"
        else:
            continue
        end = EKS_STANDARD_END.get(version)
        out.append(
            FindingIn(
                key=c.resource_id,
                title=f"Upgrade EKS cluster {c.name} out of extended support",
                description=(
                    f"Kubernetes {version} left standard support"
                    + (f" on {end:%d %b %Y}" if end else "")
                    + ". The control plane now costs 0.60 USD an hour instead of 0.10 USD "
                    f"({'from the bill' if basis == 'billing' else 'list price'}). Upgrade one "
                    "minor version at a time."
                ),
                savings_monthly=round(monthly, 2),
                currency=currency,
                risk="medium",
                effort="high",
                overlap=c.resource_id,
                **_common(c),
                steps=[
                    "Check deprecated APIs and add-on versions for the next minor version.",
                    "Upgrade the control plane, then the node groups; repeat until supported.",
                ],
                commands=[
                    f"aws eks update-cluster-version --region {c.region} --name {c.name} "
                    f"--kubernetes-version <next minor version, up to {newest}>"
                ],
                evidence={"version": version, "basis": basis, "standard_end": str(end or "")},
            )
        )
    return out


@detector("aws.ecr_cleanup", category="waste", title="Container images nobody needs")
def ecr_cleanup(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    usd = _usd(db, org_id, currency)
    if usd is None:
        return []
    out = []
    for r in _resources(db, "ecr_repository"):
        a: dict[str, Any] = r.attributes or {}
        if a.get("lifecycle_policy") is not False:
            continue  # has a policy (or we could not tell)
        waste = int(a.get("untagged_bytes") or 0) + int(a.get("stale_bytes") or 0)
        gib = waste / 2**30
        saving = round(gib * ECR_GB_MONTH_USD * usd, 2)
        if saving < MIN_SAVING:
            continue
        out.append(
            FindingIn(
                key=r.resource_id,
                title=f"Add a lifecycle policy to image repository {r.name}",
                description=(
                    f"No lifecycle policy: {gib:,.0f} GiB are untagged images or tagged "
                    "images older than 180 days beyond the newest 10 (list price "
                    "0.10 USD per GB-month)."
                ),
                savings_monthly=saving,
                currency=currency,
                risk="low",
                effort="low",
                overlap=r.resource_id,
                **_common(r),
                steps=[
                    "Expire untagged images after 7 days and keep the newest tagged images.",
                    "Check that no running workload uses an old tag before it expires.",
                ],
                commands=[
                    f"aws ecr put-lifecycle-policy --region {r.region} --repository-name "
                    f"{r.name} --lifecycle-policy-text file://lifecycle.json"
                ],
                evidence={
                    k: a.get(k)
                    for k in ("image_count", "total_bytes", "untagged_bytes", "stale_bytes")
                },
            )
        )
    return out
