"""Data retention: how long each kind of data is kept, per organisation.

Features register a :class:`RetentionPolicy` for each kind of time-stamped data
they store (audit log now; cost lines, usage samples, Kubernetes events and logs
as those land). Organisations choose a number of days within the policy's
bounds (plan limits will narrow them, B.1). The nightly ``privacy.apply_retention``
job deletes rows older than the chosen period.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import Table, delete, select
from sqlalchemy.orm import Session

from costlens.core.errors import AppError
from costlens.db.base import Base, utcnow
from costlens.db.tenant import set_tenant
from costlens.models import Organization


@dataclass(frozen=True)
class RetentionPolicy:
    kind: str
    label: str
    description: str
    table: str
    time_column: str
    default_days: int
    min_days: int
    max_days: int


_POLICIES: dict[str, RetentionPolicy] = {}


def register(policy: RetentionPolicy) -> RetentionPolicy:
    if policy.kind in _POLICIES:
        raise ValueError(f"retention policy {policy.kind!r} already registered")
    if not policy.min_days <= policy.default_days <= policy.max_days:
        raise ValueError(f"{policy.kind}: default outside bounds")
    _POLICIES[policy.kind] = policy
    return policy


def _load_feature_policies() -> None:
    import costlens.costs.policies
    import costlens.k8s.policies  # noqa: F401


def policies() -> list[RetentionPolicy]:
    _load_feature_policies()
    return sorted(_POLICIES.values(), key=lambda p: p.label)


register(
    RetentionPolicy(
        kind="audit_log",
        label="Audit log",
        description="Who changed what. Kept at least 90 days for security investigations.",
        table="audit_events",
        time_column="occurred_at",
        default_days=365,
        min_days=90,
        max_days=2555,  # 7 years
    )
)


def effective_days(org: Organization, policy: RetentionPolicy) -> int:
    chosen = (org.retention_days or {}).get(policy.kind)
    if not isinstance(chosen, int):
        return policy.default_days
    return max(policy.min_days, min(policy.max_days, chosen))


def set_retention(org: Organization, changes: dict[str, int]) -> None:
    current = dict(org.retention_days or {})
    for kind, days in changes.items():
        policy = _POLICIES.get(kind)
        if policy is None:
            raise AppError(f"Unknown kind of data: {kind}.", code="unknown_retention_kind")
        if not policy.min_days <= days <= policy.max_days:
            raise AppError(
                f"{policy.label}: choose between {policy.min_days} and {policy.max_days} days.",
                code="retention_out_of_range",
            )
        current[kind] = days
    org.retention_days = current  # new dict so the change is detected and audited


def apply_retention(db: Session, *, now: datetime | None = None) -> dict[str, int]:
    """Delete expired rows for every organisation. Returns deleted counts per kind."""
    now = now or utcnow()
    set_tenant(db, bypass=True)
    totals = {p.kind: 0 for p in policies()}
    orgs = db.scalars(select(Organization)).all()
    for org in orgs:
        for policy in policies():
            table: Table = Base.metadata.tables[policy.table]
            cutoff = now - timedelta(days=effective_days(org, policy))
            result = db.execute(
                delete(table).where(table.c.org_id == org.id, table.c[policy.time_column] < cutoff)
            )
            totals[policy.kind] += result.rowcount or 0  # type: ignore[attr-defined]
    db.flush()
    return totals


def org_id_of(org: Organization) -> uuid.UUID:
    return org.id
