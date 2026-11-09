from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from costlens.accounts.roles import Role
from costlens.clouds.aws import fixes
from costlens.db import Database
from costlens.db.tenant import set_tenant
from costlens.models import CloudConnection, Finding, FixAction
from tests.fakes.aws import FakeAws
from tests.helpers import add_to_org, make_org, sign_in_client

READ_ROLE = "arn:aws:iam::111111111111:role/CostLensReadOnly"
FIX_ROLE = "arn:aws:iam::111111111111:role/CostLensFixes"
NOW = datetime(2026, 11, 9, 9, tzinfo=UTC)


class FakeEc2:
    def __init__(self) -> None:
        self.volumes = {
            "vol-1": {"VolumeId": "vol-1", "State": "available", "Attachments": [], "Tags": []}
        }
        self.addresses = {"eipalloc-1": {"AllocationId": "eipalloc-1", "Tags": []}}
        self.changes: list[tuple[str, str]] = []

    def describe_volumes(self, VolumeIds: list[str]) -> dict[str, Any]:
        return {"Volumes": [self.volumes[v] for v in VolumeIds if v in self.volumes]}

    def describe_addresses(self, AllocationIds: list[str]) -> dict[str, Any]:
        return {"Addresses": [self.addresses[a] for a in AllocationIds if a in self.addresses]}

    def create_snapshot(self, VolumeId: str, **_: Any) -> dict[str, Any]:
        self.changes.append(("snapshot", VolumeId))
        return {"SnapshotId": "snap-123"}

    def delete_volume(self, VolumeId: str) -> None:
        self.changes.append(("delete", VolumeId))
        self.volumes.pop(VolumeId)

    def release_address(self, AllocationId: str) -> None:
        self.changes.append(("release", AllocationId))


@pytest.fixture
def env(db: Database):
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    admin = add_to_org(db, org_id, "a@acme.com", Role.ADMIN)
    dev = add_to_org(db, org_id, "dev@acme.com", Role.MEMBER)
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        conn = CloudConnection(
            org_id=org_id,
            provider="aws",
            name="Prod",
            status="active",
            external_id="ext",
            role_arn=READ_ROLE,
            account_id="111111111111",
        )
        s.add(conn)
        s.flush()
        findings = {}
        for detector, rid in (
            ("aws.ebs_unattached", "vol-1"),
            ("aws.eip_idle", "eipalloc-1"),
            ("aws.ec2_stopped", "i-9"),
        ):
            f = Finding(
                org_id=org_id, detector=detector, category="waste", dedup_key=f"{detector}:{rid}", overlap_key=rid, title=f"Fix {rid}",
                savings_monthly=10, currency="USD", risk="low", effort="low", first_seen_at=NOW, last_seen_at=NOW,
                provider="aws", account_id="111111111111", region="eu-west-1", resource_id=rid,
            )  # fmt: skip
            s.add(f)
            s.flush()
            findings[rid] = f.id
        conn_id = conn.id
    ec2 = FakeEc2()
    aws = FakeAws(clients={"ec2": ec2})
    aws.allow(READ_ROLE, "ext")
    aws.allow(FIX_ROLE, "ext")
    return {
        "org": org_id,
        "owner": owner,
        "admin": admin,
        "dev": dev,
        "conn": conn_id,
        "f": findings,
        "ec2": ec2,
        "aws": aws,
    }


def enable(client: TestClient, db: Database, env, **over) -> Any:
    client.app.state.aws = env["aws"]  # type: ignore[attr-defined]
    c = sign_in_client(client, db, env["owner"], env["org"])
    body = {
        "role_arn": FIX_ROLE,
        "days": 30,
        "max_actions_per_day": 5,
        "spend_limit_monthly": 0,
        "confirm": True,
        **over,
    }
    return c.post(f"/api/v1/connections/{env['conn']}/fixes", json=body)


def run(db: Database, env, fix_id: str) -> FixAction:
    with db.session() as s:
        out = fixes.execute(s, env["aws"], uuid.UUID(fix_id))
        assert out is not None
        s.expunge(out)
        return out


def test_turning_on_needs_owner_confirmation_and_a_separate_role(
    client: TestClient, db: Database, env
) -> None:
    assert enable(client, db, env, confirm=False).json()["code"] == "confirm_required"
    assert enable(client, db, env, role_arn=READ_ROLE).json()["code"] == "same_role"
    assert (
        enable(client, db, env, role_arn="arn:aws:iam::111111111111:role/Other").json()["code"]
        == "role_check_failed"
    )
    a = sign_in_client(client, db, env["admin"], env["org"])
    body = {"role_arn": FIX_ROLE, "confirm": True}
    assert (
        a.post(f"/api/v1/connections/{env['conn']}/fixes", json=body).status_code == 403
    )  # owners only
    r = enable(client, db, env)
    assert r.status_code == 200 and r.json()["enabled"] is True
    doc = fixes.policy_document()
    acting = next(x for x in doc["Statement"] if x["Sid"] == "ApprovedFixesOnUnprotectedResources")
    assert acting["Condition"] == {"StringNotEquals": {"aws:ResourceTag/costlens-protect": "true"}}
    assert "ec2:TerminateInstances" not in json.dumps(doc)


def test_two_people_recheck_snapshot_and_audit(client: TestClient, db: Database, env) -> None:
    enable(client, db, env)
    d = sign_in_client(client, db, env["dev"], env["org"])
    not_fixable = d.post(f"/api/v1/findings/{env['f']['i-9']}/fix")
    assert not_fixable.json()["code"] == "not_fixable"
    fix = d.post(f"/api/v1/findings/{env['f']['vol-1']}/fix").json()
    assert fix["status"] == "requested" and fix["label"] == "Snapshot, then delete the volume"
    assert d.post(f"/api/v1/findings/{env['f']['vol-1']}/fix").json()["code"] == "in_progress"
    # The requester cannot approve; nothing runs before approval.
    assert run(db, env, fix["id"]).status == "requested" and env["ec2"].changes == []
    a = sign_in_client(client, db, env["admin"], env["org"])
    assert a.post(f"/api/v1/fixes/{fix['id']}/approve").json()["status"] == "approved"
    done = run(db, env, fix["id"])
    assert done.status == "done" and done.result["snapshot_id"] == "snap-123"
    assert env["ec2"].changes == [("snapshot", "vol-1"), ("delete", "vol-1")]  # snapshot first
    with db.session() as s:
        set_tenant(s, org_id=env["org"])
        f = s.get(Finding, env["f"]["vol-1"])
        assert f is not None and f.status == "applied"


def test_guards_stop_unsafe_changes(client: TestClient, db: Database, env) -> None:
    enable(client, db, env, max_actions_per_day=1)
    env["ec2"].addresses["eipalloc-1"]["AssociationId"] = "eipassoc-9"  # in use again
    d = sign_in_client(client, db, env["dev"], env["org"])
    a_fix = d.post(f"/api/v1/findings/{env['f']['eipalloc-1']}/fix").json()
    a = sign_in_client(client, db, env["admin"], env["org"])
    a.post(f"/api/v1/fixes/{a_fix['id']}/approve")
    failed = run(db, env, a_fix["id"])
    assert (
        failed.status == "failed"
        and "in use again" in failed.result["error"]
        and env["ec2"].changes == []
    )
    # Protect tag on the volume.
    env["ec2"].volumes["vol-1"]["Tags"] = [{"Key": "costlens-protect", "Value": "true"}]
    d = sign_in_client(client, db, env["dev"], env["org"])
    v_fix = d.post(f"/api/v1/findings/{env['f']['vol-1']}/fix").json()
    a = sign_in_client(client, db, env["admin"], env["org"])
    a.post(f"/api/v1/fixes/{v_fix['id']}/approve")
    assert "costlens-protect" in run(db, env, v_fix["id"]).result["error"]
    # Turned off (any admin can): approved fixes no longer run.
    env["ec2"].volumes["vol-1"]["Tags"] = []
    d = sign_in_client(client, db, env["dev"], env["org"])
    with db.session() as s:
        set_tenant(s, org_id=env["org"])
        for x in s.scalars(select(FixAction)):
            s.delete(x)
    again = d.post(f"/api/v1/findings/{env['f']['vol-1']}/fix").json()
    a = sign_in_client(client, db, env["admin"], env["org"])
    a.post(f"/api/v1/fixes/{again['id']}/approve")
    assert a.delete(f"/api/v1/connections/{env['conn']}/fixes").status_code == 204
    assert "turned off" in run(db, env, again["id"]).result["error"]
    assert env["ec2"].changes == []


def test_expiry_and_daily_limit(db: Database, env) -> None:
    with db.session() as s:
        set_tenant(s, org_id=env["org"])
        conn = s.get(CloudConnection, env["conn"])
        assert conn is not None
        fixes.enable(
            s,
            env["aws"],
            conn,
            role_arn=FIX_ROLE,
            days=1,
            max_actions_per_day=1,
            spend_limit_monthly=0,
            user_id=env["owner"],
        )
        f1, f2 = s.get(Finding, env["f"]["vol-1"]), s.get(Finding, env["f"]["eipalloc-1"])
        assert f1 is not None and f2 is not None
        x1 = fixes.approve(s, fixes.request(s, f1, env["dev"]), env["admin"])
        x2 = fixes.approve(s, fixes.request(s, f2, env["dev"]), env["admin"])
        ids = (x1.id, x2.id)
    assert run(db, env, str(ids[0])).status == "done"
    assert "daily limit" in run(db, env, str(ids[1])).result["error"]
    with db.session() as s:
        set_tenant(s, org_id=env["org"])
        conn = s.get(CloudConnection, env["conn"])
        assert conn is not None
        cfg = dict(conn.settings["fixes"])
        cfg["expires_at"] = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        conn.settings = {**conn.settings, "fixes": cfg}
        assert fixes.settings_of(conn) is None  # expired: as if turned off


class FakeSavingsPlans:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    def describe_savings_plans_offerings(self, **kw: Any) -> dict[str, Any]:
        assert kw["planTypes"] == ["Compute"] and kw["durations"] == [31536000]
        return {"searchResults": [{"offeringId": "off-1"}]}

    def create_savings_plan(self, **kw: Any) -> dict[str, Any]:
        self.created.append(kw)
        return {"savingsPlanId": "sp-123"}


def test_auto_clean_up_files_requests_but_never_acts(client: TestClient, db: Database, env) -> None:
    enable(client, db, env, auto_request=True)
    with db.session() as s:
        set_tenant(s, org_id=env["org"])
        old = s.get(Finding, env["f"]["vol-1"])
        assert old is not None
        old.first_seen_at = datetime.now(UTC) - timedelta(days=10)  # idle for over a week
        conn = s.get(CloudConnection, env["conn"])
        assert conn is not None
        filed = fixes.auto_request(s, conn)
        assert [f.resource_id for f in filed] == ["vol-1"]  # the EIP finding is too new
        assert fixes.auto_request(s, conn) == []  # not filed twice
        fid = filed[0].id
    assert env["ec2"].changes == []
    a = sign_in_client(client, db, env["admin"], env["org"])
    rows = a.get("/api/v1/fixes").json()
    assert (
        rows[0]["requested_by"] == "CostLens (auto clean-up)" and rows[0]["status"] == "requested"
    )
    assert run(db, env, str(fid)).status == "requested"  # still nothing without approval
    a.post(f"/api/v1/fixes/{fid}/approve")
    assert run(db, env, str(fid)).status == "done"


def test_approved_savings_plan_purchase_within_spend_limit(
    client: TestClient, db: Database, env
) -> None:
    from datetime import date

    from costlens.models import PlannedPurchase

    sp = FakeSavingsPlans()
    env["aws"].clients["savingsplans"] = sp
    enable(client, db, env, spend_limit_monthly=10_000)
    with db.session() as s:
        set_tenant(s, org_id=env["org"])
        p = PlannedPurchase(
            org_id=env["org"], kind="savings_plan", description="Compute SP", hourly_commitment=1.0, term="1yr", payment="no_upfront",
            monthly_cost=730, estimated_monthly_saving=200, planned_for=date(2026, 11, 15), status="planned",
        )  # fmt: skip
        s.add(p)
        s.flush()
        pid = str(p.id)
    d = sign_in_client(client, db, env["dev"], env["org"])
    assert (
        d.post(f"/api/v1/purchases/{pid}/buy").json()["code"] == "not_buyable"
    )  # not approved yet
    with db.session() as s:
        set_tenant(s, org_id=env["org"])
        p2 = s.get(PlannedPurchase, uuid.UUID(pid))
        assert p2 is not None
        p2.status = "approved"
    d = sign_in_client(client, db, env["dev"], env["org"])
    fix = d.post(f"/api/v1/purchases/{pid}/buy").json()
    assert fix["label"] == "Buy the approved Savings Plan"
    a = sign_in_client(client, db, env["admin"], env["org"])
    a.post(f"/api/v1/fixes/{fix['id']}/approve")
    # 1.00/hour for a year commits 8,760: under 10,000, so lower the limit to see the guard.
    with db.session() as s:
        set_tenant(s, org_id=env["org"])
        conn = s.get(CloudConnection, env["conn"])
        assert conn is not None
        conn.settings = {
            **conn.settings,
            "fixes": {**conn.settings["fixes"], "spend_limit_monthly": 5000},
        }
    blocked = run(db, env, fix["id"])
    assert (
        blocked.status == "failed" and "spend limit" in blocked.result["error"] and sp.created == []
    )
    # Within the limit it buys once, idempotently, and marks the plan purchased.
    with db.session() as s:
        set_tenant(s, org_id=env["org"])
        x = s.get(FixAction, uuid.UUID(fix["id"]))
        assert x is not None
        x.status = "approved"
        conn = s.get(CloudConnection, env["conn"])
        assert conn is not None
        conn.settings = {
            **conn.settings,
            "fixes": {**conn.settings["fixes"], "spend_limit_monthly": 10_000},
        }
    done = run(db, env, fix["id"])
    assert done.status == "done" and done.result["savings_plan_id"] == "sp-123"
    assert sp.created == [
        {
            "savingsPlanOfferingId": "off-1",
            "commitment": "1.000",
            "clientToken": fix["id"],
            "tags": {"created-by": "costlens"},
        }
    ]
    with db.session() as s:
        set_tenant(s, org_id=env["org"])
        p3 = s.get(PlannedPurchase, uuid.UUID(pid))
        assert p3 is not None and p3.status == "purchased"


def test_write_access_banner_data_and_turn_off_is_audited(
    client: TestClient, db: Database, env
) -> None:
    from costlens.models import AuditEvent

    d = sign_in_client(client, db, env["dev"], env["org"])
    assert d.get("/api/v1/org/write-access").json() == []
    enable(client, db, env)
    d = sign_in_client(client, db, env["dev"], env["org"])
    rows = d.get("/api/v1/org/write-access").json()  # every member sees it
    assert len(rows) == 1 and rows[0]["enabled_by"] == "o@acme.com" and rows[0]["name"] == "Prod"
    assert (
        d.delete(f"/api/v1/connections/{env['conn']}/fixes").status_code == 403
    )  # members cannot turn off
    a = sign_in_client(client, db, env["admin"], env["org"])
    assert a.delete(f"/api/v1/connections/{env['conn']}/fixes").status_code == 204
    assert a.get("/api/v1/org/write-access").json() == []
    with db.session() as s:
        set_tenant(s, org_id=env["org"])
        events = [
            e
            for e in s.scalars(
                select(AuditEvent).where(AuditEvent.target_type == "cloud_connection")
            )
        ]
        assert any(e.actor_label == "a@acme.com" for e in events)
