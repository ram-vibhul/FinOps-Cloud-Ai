"""Evaluate governance policies against the inventory and Kubernetes objects.

Violations are kept up to date on every run: new ones open, fixed ones resolve, and an
exemption (with a note) stays until someone lifts it. Each violation shows what the
resource costs a month, so the expensive ones get fixed first."""

from __future__ import annotations

import fnmatch
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ccradar.core.notify import Message, notify
from ccradar.db.base import utcnow
from ccradar.findings.aws import MONTH
from ccradar.inventory.service import resource_costs
from ccradar.models import CloudResource, K8sCluster, K8sObject, Policy, PolicyViolation

KINDS = {
    "required_tags": "Required tags",
    "allowed_regions": "Allowed regions",
    "denied_instance_types": "Denied instance types",
    "k8s_requests": "Kubernetes containers must set requests",
}
SIZED = {
    "ec2_instance": "instance_type",
    "rds_instance": "instance_class",
    "elasticache_cluster": "node_type",
}
WORKLOADS = ("Deployment", "StatefulSet", "DaemonSet", "CronJob", "Job")


@dataclass
class Hit:
    key: str
    resource_type: str
    detail: str
    name: str | None = None
    account_id: str | None = None
    region: str | None = None


def _resources(db: Session, policy: Policy) -> list[CloudResource]:
    q = select(CloudResource).where(CloudResource.state != "deleted")
    if policy.resource_types:
        q = q.where(CloudResource.resource_type.in_(policy.resource_types))
    return list(db.scalars(q))


def _hit(r: CloudResource, detail: str) -> Hit:
    return Hit(r.resource_id, r.resource_type, detail, r.name, r.account_id, r.region)


def required_tags(db: Session, policy: Policy) -> Iterator[Hit]:
    keys = [str(k) for k in policy.params.get("tags") or []]
    allowed: dict[str, list[str]] = {
        k.lower(): [v.lower() for v in vs] for k, vs in (policy.params.get("values") or {}).items()
    }
    for r in _resources(db, policy):
        tags = {k.lower(): str(v) for k, v in (r.tags or {}).items()}
        missing = [k for k in keys if not tags.get(k.lower())]
        bad = [
            k
            for k in keys
            if tags.get(k.lower())
            and allowed.get(k.lower())
            and tags[k.lower()].lower() not in allowed[k.lower()]
        ]
        if missing or bad:
            parts = [f"missing {', '.join(missing)}"] if missing else []
            parts += [f"{k}={tags[k.lower()]} is not an allowed value" for k in bad]
            yield _hit(r, "; ".join(parts))


def allowed_regions(db: Session, policy: Policy) -> Iterator[Hit]:
    allowed = {str(x) for x in policy.params.get("regions") or []}
    for r in _resources(db, policy):
        if r.region and r.region != "global" and r.region not in allowed:
            yield _hit(r, f"runs in {r.region}, outside {', '.join(sorted(allowed))}")


def denied_instance_types(db: Session, policy: Policy) -> Iterator[Hit]:
    patterns = [str(p).lower() for p in policy.params.get("patterns") or []]
    for r in _resources(db, policy):
        field = SIZED.get(r.resource_type)
        size = str((r.attributes or {}).get(field) or "").lower() if field else ""
        hit = next((p for p in patterns if size and fnmatch.fnmatch(size, p)), None)
        if hit:
            yield _hit(r, f"{size} matches the denied pattern {hit}")


def k8s_requests(db: Session, policy: Policy) -> Iterator[Hit]:
    names = dict(db.execute(select(K8sCluster.id, K8sCluster.name)).all())
    for o in db.scalars(
        select(K8sObject).where(K8sObject.deleted_at.is_(None), K8sObject.kind.in_(WORKLOADS))
    ):
        missing = [
            c.get("name", "?")
            for c in (o.data or {}).get("containers") or []
            if not ((c.get("resources") or {}).get("requests") or {}).get("cpu")
            or not ((c.get("resources") or {}).get("requests") or {}).get("memory")
        ]
        if missing:
            cluster = names.get(o.cluster_id, "cluster")
            yield Hit(
                f"{o.cluster_id}/{o.namespace}/{o.kind}/{o.name}",
                o.kind,
                f"containers without CPU and memory requests: {', '.join(missing)}",
                f"{cluster}/{o.namespace}/{o.name}",
            )


CHECKS = {
    "required_tags": required_tags,
    "allowed_regions": allowed_regions,
    "denied_instance_types": denied_instance_types,
    "k8s_requests": k8s_requests,
}


def evaluate(db: Session, org_id: uuid.UUID, currency: str) -> dict[str, int]:
    now = utcnow()
    counts = {"open": 0, "new": 0, "resolved": 0}
    fresh: list[tuple[Policy, Hit]] = []
    for policy in db.scalars(select(Policy)):
        existing = {
            v.resource_key: v
            for v in db.scalars(
                select(PolicyViolation).where(PolicyViolation.policy_id == policy.id)
            )
        }
        hits = (
            list(CHECKS[policy.kind](db, policy))
            if policy.enabled and policy.kind in CHECKS
            else []
        )
        costs = resource_costs(db, org_id, [h.key for h in hits], days=30, currency=currency)
        seen = set()
        for h in hits:
            seen.add(h.key)
            v = existing.get(h.key)
            if v is None:
                v = PolicyViolation(
                    org_id=org_id,
                    policy_id=policy.id,
                    resource_key=h.key[:1000],
                    first_seen_at=now,
                    status="open",
                )
                db.add(v)
                counts["new"] += 1
                fresh.append((policy, h))
            elif v.status == "resolved":
                v.status, v.resolved_at = "open", None
                counts["new"] += 1
                fresh.append((policy, h))
            v.resource_type, v.account_id, v.region, v.name = (
                h.resource_type,
                h.account_id,
                h.region,
                h.name,
            )
            v.detail, v.last_seen_at = h.detail[:1000], now
            v.monthly_cost = round(costs[h.key] * MONTH / 30, 2) if h.key in costs else None
            counts["open"] += v.status == "open"
        for key, v in existing.items():
            if key not in seen and v.status == "open":
                v.status, v.resolved_at = "resolved", now
                counts["resolved"] += 1
    db.flush()
    if fresh:
        by_policy: dict[str, list[Hit]] = {}
        for policy, h in fresh:
            by_policy.setdefault(policy.name, []).append(h)
        notify(
            db,
            "policy_violation",
            [
                Message(
                    title=f"{name}: {len(hits)} new violation(s)",
                    text="; ".join(f"{h.name or h.key}: {h.detail}" for h in hits[:5])
                    + (" …" if len(hits) > 5 else ""),
                    link="/governance?tab=policies",
                )
                for name, hits in by_policy.items()
            ],
        )
    return counts


def summary(db: Session) -> list[dict[str, Any]]:
    out = []
    for p in db.scalars(select(Policy).order_by(Policy.name)):
        rows = list(
            db.scalars(
                select(PolicyViolation).where(
                    PolicyViolation.policy_id == p.id, PolicyViolation.status == "open"
                )
            )
        )
        out.append(
            {
                "id": str(p.id),
                "name": p.name,
                "kind": p.kind,
                "kind_label": KINDS.get(p.kind, p.kind),
                "params": p.params,
                "resource_types": p.resource_types,
                "severity": p.severity,
                "enabled": p.enabled,
                "open": len(rows),
                "monthly_cost": round(sum(v.monthly_cost or 0 for v in rows), 2),
            }
        )
    return out
