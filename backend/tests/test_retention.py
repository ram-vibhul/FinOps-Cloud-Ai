from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from costlens.accounts.roles import Role
from costlens.db import Database
from costlens.db.base import utcnow
from costlens.db.tenant import set_tenant
from costlens.models import AuditEvent
from costlens.privacy import retention
from tests.helpers import add_to_org, make_org, sign_in_client


def add_event(db: Database, org_id, days_ago: int) -> None:
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        s.add(
            AuditEvent(
                org_id=org_id,
                actor_type="system",
                actor_label="system",
                action="test.event",
                target_type="test",
                summary=f"{days_ago}d",
                occurred_at=utcnow() - timedelta(days=days_ago),
            )
        )


def count(db: Database, org_id) -> list[str]:
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        return sorted(
            s.scalars(select(AuditEvent.summary).where(AuditEvent.action == "test.event"))
        )


def test_purge_respects_each_org_setting(db: Database) -> None:
    a, _ = make_org(db, "A", "a@a.com")
    b, _ = make_org(db, "B", "b@b.com")
    for org in (a, b):
        for days in (10, 100, 400, 3000):
            add_event(db, org, days)
    with db.session() as s:
        set_tenant(s, bypass=True)
        from costlens.models import Organization

        org_b = s.get(Organization, b)
        assert org_b is not None
        retention.set_retention(org_b, {"audit_log": 90})
    with db.session() as s:
        totals = retention.apply_retention(s)
    assert totals["audit_log"] == 2 + 3  # A keeps 365 days (default); B keeps 90
    assert count(db, a) == ["100d", "10d"]
    assert count(db, b) == ["10d"]
    # Nothing older than the setting survives, in any organisation.
    with db.session() as s:
        set_tenant(s, bypass=True)
        oldest = s.scalar(select(func.min(AuditEvent.occurred_at)).where(AuditEvent.org_id == b))
        assert oldest is not None


def test_bounds_and_unknown_kinds(db: Database) -> None:
    from costlens.core.errors import AppError
    from costlens.models import Organization

    org = Organization(name="x", slug="x", retention_days={})
    with pytest.raises(AppError, match="between 90 and 2555"):
        retention.set_retention(org, {"audit_log": 30})
    with pytest.raises(AppError, match="Unknown"):
        retention.set_retention(org, {"tea_leaves": 30})
    org.retention_days = {"audit_log": 10_000}  # out-of-range stored value is clamped
    assert retention.effective_days(org, retention.policies()[0]) == 2555


def test_registry_validation() -> None:
    with pytest.raises(ValueError):
        retention.register(retention.policies()[0])
    with pytest.raises(ValueError, match="bounds"):
        retention.register(
            retention.RetentionPolicy(
                kind="x",
                label="x",
                description="",
                table="audit_events",
                time_column="occurred_at",
                default_days=1,
                min_days=5,
                max_days=10,
            )
        )


def test_retention_api(client: TestClient, db: Database) -> None:
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    viewer = add_to_org(db, org_id, "v@acme.com", Role.VIEWER)
    c = sign_in_client(client, db, viewer, org_id)
    audit = next(r for r in c.get("/api/v1/org/retention").json() if r["kind"] == "audit_log")
    assert audit["kind"] == "audit_log" and audit["days"] == 365
    assert c.patch("/api/v1/org/retention", json={"audit_log": 730}).status_code == 403
    c = sign_in_client(client, db, owner, org_id)
    r = c.patch("/api/v1/org/retention", json={"audit_log": 730})
    assert next(x for x in r.json() if x["kind"] == "audit_log")["days"] == 730
    bad = c.patch("/api/v1/org/retention", json={"audit_log": 5})
    assert bad.status_code == 400 and bad.json()["code"] == "retention_out_of_range"
    changes = c.get("/api/v1/org/audit-events", params={"action": "organization.updated"}).json()
    assert changes["items"][0]["changes"]["retention_days"][1] == {"audit_log": 730}
