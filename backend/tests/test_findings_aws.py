from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ccradar.accounts.roles import Role
from ccradar.costs.ingest import CostLine, replace_billing_period
from ccradar.db import Database
from ccradar.db.base import utcnow
from ccradar.db.tenant import set_tenant
from ccradar.findings.service import run
from ccradar.inventory.aws import Found
from ccradar.inventory.service import upsert
from ccradar.models import Finding
from tests.helpers import add_to_org, make_org, sign_in_client

OLD = datetime.now(UTC) - timedelta(days=400)


def found(rtype: str, rid: str, state: str, **attrs) -> Found:
    service = {
        "load_balancer": "Elastic Load Balancing",
        "elastic_ip": "Amazon Virtual Private Cloud",
        "nat_gateway": "Amazon Virtual Private Cloud",
    }.get(rtype, "Amazon Elastic Compute Cloud")
    return Found(
        service,
        rtype,
        rid,
        "us-east-1",
        name=None,
        state=state,
        created_at=OLD,
        tags={},
        attributes=attrs,
    )


def bill_line(day: date, resource: str, usage: str, cost: float, service: str) -> CostLine:
    return CostLine(
        charge_date=day,
        provider="aws",
        service_name=service,
        resource_id=resource,
        usage_type=usage,
        billed_cost=Decimal(str(cost)),
        effective_cost=Decimal(str(cost)),
        billing_currency="USD",
    )


@pytest.fixture
def org(db: Database):
    org_id, owner = make_org(db, "Acme", "owner@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        upsert(
            s,
            org_id,
            "111111111111",
            [
                found(
                    "ebs_volume",
                    "vol-unattached-billed",
                    "available",
                    size_gb=200,
                    volume_type="gp2",
                    attached_to=[],
                ),
                found(
                    "ebs_volume",
                    "vol-unattached-list",
                    "available",
                    size_gb=100,
                    volume_type="gp3",
                    attached_to=[],
                ),
                found(
                    "ebs_volume",
                    "vol-in-use-gp2",
                    "in-use",
                    size_gb=500,
                    volume_type="gp2",
                    attached_to=["i-old"],
                ),
                found(
                    "ebs_volume",
                    "vol-of-stopped",
                    "in-use",
                    size_gb=50,
                    volume_type="gp3",
                    attached_to=["i-stopped"],
                ),
                found("elastic_ip", "eipalloc-idle", "unattached", public_ip="3.3.3.3"),
                found("elastic_ip", "eipalloc-used", "in-use", public_ip="4.4.4.4"),
                found("ebs_snapshot", "snap-orphan", "completed", size_gb=300, volume="vol-gone"),
                found(
                    "ebs_snapshot",
                    "snap-of-live",
                    "completed",
                    size_gb=300,
                    volume="vol-in-use-gp2",
                ),
                found("ec2_instance", "i-stopped", "stopped", instance_type="t3.large"),
                found("ec2_instance", "i-old", "running", instance_type="m4.xlarge"),
                found("load_balancer", "arn:lb/idle", "active", type="application"),
                found("load_balancer", "arn:lb/busy", "active", type="application"),
                found("nat_gateway", "nat-idle", "available"),
            ],
            seen_at=utcnow() - timedelta(days=30),
        )
    days = [date(2026, 11, d) for d in range(1, 31)]
    lines = []
    for d in days:
        lines += [
            bill_line(
                d,
                "vol-unattached-billed",
                "EBS:VolumeUsage.gp2",
                0.75,
                "Amazon Elastic Compute Cloud",
            ),
            bill_line(d, "i-old", "BoxUsage:m4.xlarge", 4.80, "Amazon Elastic Compute Cloud"),
            bill_line(d, "arn:lb/idle", "LoadBalancerUsage", 0.54, "Elastic Load Balancing"),
            bill_line(d, "arn:lb/busy", "LoadBalancerUsage", 0.54, "Elastic Load Balancing"),
            bill_line(d, "arn:lb/busy", "LCUUsage", 0.30, "Elastic Load Balancing"),
            bill_line(d, "nat-idle", "NatGateway-Hours", 1.08, "Amazon Virtual Private Cloud"),
        ]
    with db.session() as s:
        replace_billing_period(
            s,
            org_id=org_id,
            source_kind="t",
            source_key="t",
            billing_period=date(2026, 11, 1),
            lines=lines,
        )
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        run(s, org_id)
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        rows = {f.dedup_key: f for f in s.scalars(select(Finding))}
    return org_id, owner, rows


def test_waste_detectors(org) -> None:
    _, _, rows = org
    billed = rows["aws.ebs_unattached:vol-unattached-billed"]
    assert float(billed.savings_monthly) == pytest.approx(0.75 * 30.4, abs=0.01)
    assert billed.evidence["basis"] == "billing" and billed.risk == "medium"
    assert billed.commands[0].startswith("aws ec2 create-snapshot")  # snapshot before delete
    listed = rows["aws.ebs_unattached:vol-unattached-list"]
    assert float(listed.savings_monthly) == 8.0 and listed.evidence["basis"] == "list price"
    assert "aws.ebs_unattached:vol-in-use-gp2" not in rows
    assert float(rows["aws.eip_idle:eipalloc-idle"].savings_monthly) == pytest.approx(
        3.65, abs=0.01
    )
    assert "aws.eip_idle:eipalloc-used" not in rows
    assert float(rows["aws.snapshot_old:snap-orphan"].savings_monthly) == 15.0
    assert "aws.snapshot_old:snap-of-live" not in rows
    stopped = rows["aws.ec2_stopped:i-stopped"]
    assert float(stopped.savings_monthly) == 4.0 and stopped.evidence["volumes"] == [
        "vol-of-stopped"
    ]
    assert "aws.lb_unused:arn:lb/idle" in rows and "aws.lb_unused:arn:lb/busy" not in rows
    assert float(rows["aws.nat_idle:nat-idle"].savings_monthly) == pytest.approx(
        1.08 * 30.4, abs=0.01
    )
    gp3 = rows["aws.previous_generation:gp3:vol-in-use-gp2"]
    assert float(gp3.savings_monthly) == 10.0 and gp3.commands[0].endswith("--volume-type gp3")
    gen = rows["aws.previous_generation:gen:i-old"]
    assert "m4.xlarge to m6i.xlarge" in gen.title
    assert float(gen.savings_monthly) == pytest.approx(4.80 * 30.4 * 0.1, abs=0.01)


def test_savings_api_totals(client: TestClient, db: Database, org) -> None:
    org_id, owner, rows = org
    r = sign_in_client(client, db, owner, org_id).get("/api/v1/findings").json()
    total = sum(float(f.savings_monthly) for f in rows.values())
    assert r["summary"]["savings_monthly"] == pytest.approx(total, abs=0.05)  # no overlaps here
    assert set(r["summary"]["by_category"]) == {"waste", "rightsizing"}


def test_demo_has_waste_findings(client: TestClient, db: Database) -> None:
    from ccradar.demo.seed import ensure_demo, load_all_seeders

    load_all_seeders()
    with db.session() as s:
        demo_id = ensure_demo(s).id
    _, visitor = make_org(db, "Visitor", "visitor@x.com")
    add_to_org(db, demo_id, "visitor@x.com", Role.VIEWER)
    r = sign_in_client(client, db, visitor, demo_id).get("/api/v1/findings").json()
    detectors = {i["detector"] for i in r["items"]}
    assert {
        "aws.ebs_unattached",
        "aws.eip_idle",
        "aws.snapshot_old",
        "k8s.rightsizing",
    } <= detectors
