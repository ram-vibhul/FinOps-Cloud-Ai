"""Policy as code (11.41): the organisation's governance policies, checked before a change
is deployed (a Terraform plan in CI) and the moment it happens (Live Radar).

The policies are the same ones Governance checks against the inventory every day:
required tags, allowed regions and denied instance types, plus ``max_monthly_increase``
for plans. A *critical* policy blocks: the CI check fails. Other severities warn.
Policies can be kept in the repository as YAML (``policies_yaml`` / ``import_yaml``).
"""

from __future__ import annotations

import fnmatch
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from ccradar.models import Policy

# Terraform resource type -> (inventory type, size attribute in the plan)
TF_TYPES = {
    "aws_instance": ("ec2_instance", "instance_type"),
    "aws_db_instance": ("rds_instance", "instance_class"),
    "aws_elasticache_cluster": ("elasticache_cluster", "node_type"),
    "aws_launch_template": ("ec2_instance", "instance_type"),
}
# CloudTrail event -> (inventory type, size field in requestParameters)
EVENTS = {
    "RunInstances": ("ec2_instance", "instanceType"),
    "CreateDBInstance": ("rds_instance", "dBInstanceClass"),
    "ModifyDBInstance": ("rds_instance", "dBInstanceClass"),
    "CreateCacheCluster": ("elasticache_cluster", "cacheNodeType"),
}
YAML_KINDS = ("required_tags", "allowed_regions", "denied_instance_types",
              "max_monthly_increase", "k8s_requests")  # fmt: skip


def _applies(policy: Policy, resource_type: str) -> bool:
    return not policy.resource_types or resource_type in policy.resource_types


def check(policy: Policy, resource_type: str, size: str | None, region: str | None,
          tags: dict[str, str] | None) -> str | None:  # fmt: skip
    """Why one planned or live resource breaks ``policy``, or None."""
    if not policy.enabled or not _applies(policy, resource_type):
        return None
    p = policy.params or {}
    if policy.kind == "denied_instance_types" and size:
        hit = next(
            (x for x in p.get("patterns") or [] if fnmatch.fnmatch(size.lower(), str(x).lower())),
            None,
        )
        return f"{size} matches the denied pattern {hit}" if hit else None
    if policy.kind == "allowed_regions" and region and region != "global":
        allowed = {str(x) for x in p.get("regions") or []}
        return None if region in allowed else f"{region} is outside {', '.join(sorted(allowed))}"
    if policy.kind == "required_tags" and tags is not None:
        have = {k.lower(): str(v) for k, v in tags.items()}
        missing = [str(k) for k in p.get("tags") or [] if not have.get(str(k).lower())]
        return f"missing tags {', '.join(missing)}" if missing else None
    return None


def _policies(db: Session) -> list[Policy]:
    return [x for x in db.scalars(select(Policy).where(Policy.enabled.is_(True)))]


def _hit(policy: Policy, where: str, detail: str) -> dict[str, Any]:
    return {"policy": policy.name, "kind": policy.kind, "severity": policy.severity,
            "resource": where, "detail": detail}  # fmt: skip


def _plan_region(plan: dict[str, Any]) -> str | None:
    aws = ((plan.get("configuration") or {}).get("provider_config") or {}).get("aws") or {}
    region = ((aws.get("expressions") or {}).get("region") or {}).get("constant_value")
    return str(region) if region else None


def plan_violations(db: Session, plan: dict[str, Any], monthly_change: float) -> dict[str, Any]:
    """Violations in a Terraform plan (``terraform show -json``)."""
    policies, hits = _policies(db), []
    region = _plan_region(plan)
    for rc in plan.get("resource_changes") or []:
        change = rc.get("change") or {}
        if not set(change.get("actions") or []) & {"create", "update"}:
            continue
        mapped = TF_TYPES.get(str(rc.get("type")))
        if mapped is None:
            continue
        after = change.get("after") or {}
        size = after.get(mapped[1])
        tags = after.get("tags_all") or after.get("tags") or {}
        for policy in policies:
            why = check(policy, mapped[0], str(size) if size else None, region, dict(tags))
            if why:
                hits.append(_hit(policy, str(rc.get("address")), why))
    for policy in policies:
        limit = (policy.params or {}).get("amount")
        if (
            policy.kind == "max_monthly_increase"
            and limit is not None
            and monthly_change > float(limit)
        ):
            why = f"adds {monthly_change:,.2f} a month, above {float(limit):,.2f}"
            hits.append(_hit(policy, "whole plan", why))
    return {"violations": hits, "blocked": any(h["severity"] == "critical" for h in hits)}


def _event_tags(request: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    specs = request.get("tagSpecificationSet") or {}
    for spec in specs.get("items") or []:
        for t in (spec.get("tags") or []) or (spec.get("tagSet") or {}).get("items") or []:
            out[str(t.get("key") or t.get("Key"))] = str(t.get("value") or t.get("Value") or "")
    for t in request.get("tags") or []:  # RDS and ElastiCache
        out[str(t.get("key") or t.get("Key"))] = str(t.get("value") or t.get("Value") or "")
    return out


def live_violations(db: Session, event_name: str, region: str | None,
                    request: dict[str, Any]) -> list[dict[str, Any]]:  # fmt: skip
    """Violations in one live change (a CloudTrail record's requestParameters)."""
    mapped = EVENTS.get(event_name)
    if mapped is None:
        return []
    size = request.get(mapped[1])
    tags = _event_tags(request) if event_name.startswith(("Run", "Create")) else None
    hits = []
    for policy in _policies(db):
        why = check(policy, mapped[0], str(size) if size else None, region, tags)
        if why:
            hits.append(_hit(policy, event_name, why))
    return hits


def markdown(result: dict[str, Any]) -> str:
    hits = result["violations"]
    if not hits:
        return ""
    head = "**Blocked by policy**" if result["blocked"] else "**Policy warnings**"
    rows = [
        f"| {h['severity']} | {h['policy']} | `{h['resource']}` | {h['detail']} |" for h in hits
    ]
    return "\n\n" + "\n".join([head, "", "| Severity | Policy | Resource | Why |",
                                 "|---|---|---|---|", *rows])  # fmt: skip


# --- policies as YAML ----------------------------------------------------------------


def policies_yaml(db: Session) -> str:
    rows = [
        {
            "name": p.name,
            "kind": p.kind,
            "severity": p.severity,
            "enabled": p.enabled,
            "params": p.params or {},
            "resource_types": p.resource_types or [],
        }
        for p in db.scalars(select(Policy).order_by(Policy.name))
    ]
    return yaml.safe_dump({"policies": rows}, sort_keys=False, allow_unicode=True)
