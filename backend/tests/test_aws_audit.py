from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select

from costlens.accounts.roles import Role
from costlens.aws_audit.catalogue import CHECKS
from costlens.aws_audit.service import run_audit
from costlens.costs.ingest import CostLine, replace_billing_period
from costlens.db import Database
from costlens.db.base import utcnow
from costlens.db.tenant import set_tenant
from costlens.findings.registry import FindingIn
from costlens.findings.service import _store
from costlens.inventory.aws import Found
from costlens.inventory.service import upsert
from costlens.models import AuditResult, Finding
from tests.helpers import add_to_org, make_org, sign_in_client


def _results(s, run) -> dict[str, AuditResult]:
    return {
        r.check_id: r for r in s.scalars(select(AuditResult).where(AuditResult.run_id == run.id))
    }


def test_without_a_bill_every_check_needs_data(db: Database) -> None:
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        run = run_audit(s, org_id, run_detectors=False)
        rows = _results(s, run)
    assert len(rows) == len(CHECKS) and run.status == "done"
    assert {r.status for r in rows.values()} == {"more_data"}
    assert "Cost and Usage Report" in rows["s3.tiering"].reason


def _seed(db: Database):
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    today = utcnow().date()
    lines = [
        CostLine(
            charge_date=today - timedelta(days=d),
            provider="aws",
            service_name=svc,
            resource_id=rid,
            billed_cost=Decimal(5),
            effective_cost=Decimal(5),
            billing_currency="USD",
        )
        for d in range(1, 11)
        for svc, rid in (
            ("Amazon Elastic Compute Cloud", "i-1"),
            ("Amazon Simple Storage Service", None),
            ("Amazon Foo Service", None),
        )
    ]
    with db.session() as s:
        for month in sorted({ln.charge_date.replace(day=1) for ln in lines}):
            replace_billing_period(
                s,
                org_id=org_id,
                source_kind="t",
                source_key=str(month),
                billing_period=month,
                lines=[ln for ln in lines if ln.charge_date.replace(day=1) == month],
            )
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        upsert(
            s,
            org_id,
            "1",
            [
                Found(
                    "Amazon Elastic Compute Cloud",
                    "ec2_instance",
                    "i-1",
                    "us-east-1",
                    state="running",
                    attributes={"instance_type": "m6i.large"},
                ),
                Found(
                    "Amazon Elastic Compute Cloud",
                    "ebs_volume",
                    "vol-1",
                    "us-east-1",
                    state="available",
                ),
            ],
        )
        _store(
            s,
            org_id,
            "aws.ebs_unattached",
            "waste",
            [
                FindingIn(
                    key="vol-1",
                    title="Delete vol-1",
                    savings_monthly=8,
                    currency="USD",
                    provider="aws",
                    resource_id="vol-1",
                ),
            ],
        )
    return org_id, owner


def test_every_row_ends_in_a_clear_state(db: Database) -> None:
    org_id, _ = _seed(db)
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        run = run_audit(s, org_id, run_detectors=False)
        rows = _results(s, run)
        summary = run.summary
    assert rows["ebs.unattached"].status == "saving" and rows["ebs.unattached"].savings_monthly == 8
    assert rows["ec2.rightsizing"].status == "more_data"
    assert "CloudWatch" in rows["ec2.rightsizing"].reason
    assert rows["ec2.eip"].status == "fine" and "None found" in rows["ec2.eip"].reason
    assert rows["lambda.config"].status == "fine" and "Not used" in rows["lambda.config"].reason
    # Spend but no bucket in the inventory while other inventory exists: none found.
    assert rows["s3.tiering"].status == "fine" and rows["s3.tiering"].spend_monthly == 50
    assert rows["dynamodb.capacity"].status == "fine"  # not used
    assert rows["s3.glacier"].status == "fine"  # nothing in the bill to review
    other = rows["other:Amazon Foo Service"]
    assert other.status == "manual" and other.area == "other"
    assert summary["counts"]["saving"] == 1 and summary["savings_monthly"] == 8
    assert summary["checks"] == len(CHECKS) + 1 and summary["uncovered_spend_monthly"] == 50
    assert {r.status for r in rows.values()} <= {"fine", "saving", "more_data", "manual"}
    assert all(r.reason for r in rows.values())

    with db.session() as s:  # fixed: the next run shows what changed
        set_tenant(s, org_id=org_id)
        for f in s.scalars(select(Finding)):
            f.status = "verified"
        run2 = run_audit(s, org_id, run_detectors=False)
        again = _results(s, run2)
        assert again["ebs.unattached"].status == "fine"
        assert again["ebs.unattached"].details["previous_status"] == "saving"
        assert run2.summary["changed"] == 1


def test_detector_failure_is_reported(db: Database, monkeypatch) -> None:
    org_id, _ = _seed(db)
    import costlens.findings.service as fs

    real = fs.run
    monkeypatch.setattr(
        fs,
        "run",
        lambda db, org_id: {**real(db, org_id, only=[]), "aws.eip_idle": {"error": "AccessDenied"}},
    )
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        rows = _results(s, run_audit(s, org_id))
    assert rows["ec2.eip"].status == "more_data" and "AccessDenied" in rows["ec2.eip"].reason


def test_api(client: TestClient, db: Database) -> None:
    org_id, owner = _seed(db)
    viewer = add_to_org(db, org_id, "v@acme.com", Role.VIEWER)
    v = sign_in_client(client, db, viewer, org_id)
    assert v.post("/api/v1/audits").status_code == 403
    assert len(v.get("/api/v1/audits/catalogue").json()["checks"]) == len(CHECKS)
    o = sign_in_client(client, db, owner, org_id)
    r = o.post("/api/v1/audits")
    assert r.status_code == 201, r.text
    run_id = r.json()["id"]
    assert r.json()["requested_by"] and r.json()["summary"]["checks"] == len(CHECKS) + 1
    assert [x["id"] for x in o.get("/api/v1/audits").json()] == [run_id]
    detail = o.get(f"/api/v1/audits/{run_id}").json()
    first = detail["results"][0]
    assert first["area"] == "compute" and first["status"] in ("saving", "more_data")
    assert detail["results"][-1]["area"] == "other"
    assert o.get(f"/api/v1/audits/{'0' * 32}").status_code == 404


def test_catalogue_covers_every_detector_once() -> None:
    from costlens.findings.registry import load_all

    used = [d for c in CHECKS for d in c.detectors]
    assert sorted(used) == sorted(load_all()) and len({c.id for c in CHECKS}) == len(CHECKS)
