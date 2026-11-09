from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from costlens.costs.ingest import CostLine, replace_billing_period
from costlens.db import Database
from costlens.db.tenant import set_tenant
from costlens.inventory import aws as collectors
from costlens.inventory import ce_fallback
from costlens.inventory.aws import Found
from costlens.inventory.service import collect_aws
from costlens.models import CloudConnection, CloudResource, CostHourly
from tests.fakes.aws import FakeAws
from tests.helpers import make_org, sign_in_client

ROLE = "arn:aws:iam::222222222222:role/CostLensReadOnly"
LAUNCH = datetime(2026, 1, 1, tzinfo=UTC)


class FakeEC2:
    def __init__(self) -> None:
        self.volumes = [
            {
                "VolumeId": "vol-1",
                "State": "available",
                "Size": 500,
                "VolumeType": "gp2",
                "CreateTime": LAUNCH,
                "Attachments": [],
                "Tags": [{"Key": "team", "Value": "data"}],
            },
            {
                "VolumeId": "vol-2",
                "State": "in-use",
                "Size": 50,
                "VolumeType": "gp3",
                "CreateTime": LAUNCH,
                "Attachments": [{"InstanceId": "i-1"}],
            },
        ]

    def describe_instances(self, **kw: Any) -> dict[str, Any]:
        if "NextToken" not in kw:
            return {
                "Reservations": [
                    {
                        "Instances": [
                            {
                                "InstanceId": "i-1",
                                "InstanceType": "m6i.large",
                                "LaunchTime": LAUNCH,
                                "State": {"Name": "running"},
                                "Architecture": "x86_64",
                                "Tags": [
                                    {"Key": "Name", "Value": "web-1"},
                                    {"Key": "owner", "Value": "ravi"},
                                ],
                            }
                        ]
                    }
                ],
                "NextToken": "p2",
            }
        return {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": "i-2",
                            "InstanceType": "t3.small",
                            "LaunchTime": LAUNCH,
                            "State": {"Name": "stopped"},
                            "Tags": [{"Key": "team", "Value": "search"}],
                        }
                    ]
                }
            ]
        }

    def describe_volumes(self, **kw: Any) -> dict[str, Any]:
        return {"Volumes": self.volumes}

    def describe_addresses(self, **kw: Any) -> dict[str, Any]:
        return {
            "Addresses": [
                {"AllocationId": "eipalloc-1", "PublicIp": "1.2.3.4"},
                {
                    "AllocationId": "eipalloc-2",
                    "PublicIp": "1.2.3.5",
                    "AssociationId": "a-1",
                    "InstanceId": "i-1",
                },
            ]
        }

    def describe_nat_gateways(self, **kw: Any) -> dict[str, Any]:
        return {
            "NatGateways": [{"NatGatewayId": "nat-1", "State": "available", "CreateTime": LAUNCH}]
        }

    def describe_snapshots(self, **kw: Any) -> dict[str, Any]:
        assert kw["OwnerIds"] == ["self"]
        return {
            "Snapshots": [
                {
                    "SnapshotId": "snap-1",
                    "State": "completed",
                    "VolumeSize": 100,
                    "StartTime": LAUNCH,
                }
            ]
        }


class FakeELB:
    def describe_load_balancers(self, **kw: Any) -> dict[str, Any]:
        return {
            "LoadBalancers": [
                {
                    "LoadBalancerArn": "arn:aws:elasticloadbalancing:x:lb/app/a/1",
                    "LoadBalancerName": "a",
                    "State": {"Code": "active"},
                    "Type": "application",
                }
            ]
        }


class DeniedRDS:
    def describe_db_instances(self, **kw: Any) -> dict[str, Any]:
        raise PermissionError("AccessDenied")


@pytest.fixture
def env(client: TestClient, db: Database):
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        conn = CloudConnection(
            org_id=org_id,
            provider="aws",
            name="Prod",
            status="active",
            external_id="ext",
            role_arn=ROLE,
            account_id="222222222222",
        )
        s.add(conn)
        s.flush()
        conn_id = conn.id
    day = date(2026, 11, 10)
    with db.session() as s:
        replace_billing_period(
            s,
            org_id=org_id,
            source_kind="t",
            source_key="t",
            billing_period=day,
            lines=[
                CostLine(
                    charge_date=day - timedelta(days=i),
                    provider="aws",
                    service_name="Amazon Elastic Compute Cloud",
                    sub_account_id="222222222222",
                    region="ap-south-1",
                    resource_id=rid,
                    billed_cost=Decimal(str(c)),
                    effective_cost=Decimal(str(c)),
                    billing_currency="USD",
                )
                for i in range(5)
                for rid, c in (("i-1", 10), ("vol-1", 2))
            ],
        )
    ec2 = FakeEC2()
    # us-east-1 is always visited (global services); this account has nothing there.
    aws = FakeAws(
        clients={"ec2": ec2, "elbv2": FakeELB(), "rds": DeniedRDS()}, denied_regions={"us-east-1"}
    )
    aws.allow(ROLE, "ext")
    return {
        "c": client,
        "db": db,
        "org": org_id,
        "owner": owner,
        "conn": conn_id,
        "aws": aws,
        "ec2": ec2,
    }


def resources(env) -> dict[str, CloudResource]:
    with env["db"].session() as s:
        set_tenant(s, org_id=env["org"])
        return {r.resource_id: r for r in s.scalars(select(CloudResource))}


def test_collect_maps_everything(env) -> None:
    with env["db"].session() as s:
        counts = collect_aws(s, env["aws"], env["conn"])
    assert counts["ec2_instances"] == 2 and counts["ebs_volumes"] == 2
    assert "rds_instances" not in counts  # permission missing: skipped, rest still collected
    r = resources(env)
    assert (
        r["i-1"].name == "web-1"
        and r["i-1"].owner == "ravi"
        and r["i-1"].attributes["instance_type"] == "m6i.large"
    )
    assert r["i-2"].state == "stopped" and r["i-2"].owner == "search"  # team tag as fallback owner
    assert r["vol-1"].state == "available" and r["vol-1"].attributes["attached_to"] == []
    assert r["eipalloc-1"].state == "unattached" and r["eipalloc-2"].state == "in-use"
    assert {"nat-1", "snap-1", "arn:aws:elasticloadbalancing:x:lb/app/a/1"} <= set(r)


def test_vanished_resources_marked_deleted_only_on_complete_runs(env) -> None:
    with env["db"].session() as s:
        collect_aws(s, env["aws"], env["conn"])
    env["ec2"].volumes = env["ec2"].volumes[1:]
    with env["db"].session() as s:
        collect_aws(s, env["aws"], env["conn"])
    # EC2 was collected in full: the vanished volume is gone even though RDS failed.
    assert resources(env)["vol-1"].state == "deleted"


def test_failed_collector_keeps_its_resources(env) -> None:
    from costlens.inventory.service import upsert

    with env["db"].session() as s:
        set_tenant(s, org_id=env["org"])
        upsert(
            s,
            env["org"],
            "222222222222",
            [
                Found(
                    "Amazon Relational Database Service",
                    "rds_instance",
                    "arn:db",
                    "ap-south-1",
                    state="available",
                ),
            ],
        )
    with env["db"].session() as s:
        collect_aws(s, env["aws"], env["conn"])  # the fake RDS refuses: keep the database
    assert resources(env)["arn:db"].state == "available"
    env["aws"].clients["rds"] = type(
        "OkRDS", (), {"describe_db_instances": lambda self, **kw: {"DBInstances": []}}
    )()
    with env["db"].session() as s:
        collect_aws(s, env["aws"], env["conn"])
    assert resources(env)["arn:db"].state == "deleted"


def test_inventory_api_with_costs(env) -> None:
    with env["db"].session() as s:
        collect_aws(s, env["aws"], env["conn"])
    c = sign_in_client(env["c"], env["db"], env["owner"], env["org"])
    r = c.get("/api/v1/resources").json()
    assert r["items"][0]["resource_id"] == "i-1" and r["items"][0]["cost_30d"] == 50.0
    assert r["items"][0]["cost_source"] == "billing export" and r["items"][0]["age_days"] > 300
    assert r["by_type"]["ec2_instance"] == 2
    vols = c.get(
        "/api/v1/resources", params={"resource_type": "ebs_volume", "state": "available"}
    ).json()
    assert [i["resource_id"] for i in vols["items"]] == ["vol-1"]
    assert (
        c.get("/api/v1/resources", params={"q": "ravi"}).json()["items"][0]["resource_id"] == "i-1"
    )
    detail = c.get(f"/api/v1/resources/{r['items'][0]['id']}").json()
    assert len(detail["daily"]) == 5 and detail["daily"][-1]["cost"] == 10.0
    other, boss = make_org(env["db"], "Other", "b@other.com")
    o = sign_in_client(env["c"], env["db"], boss, other)
    assert o.get("/api/v1/resources").json()["items"] == []
    assert o.get(f"/api/v1/resources/{r['items'][0]['id']}").status_code == 404


def test_ce_fallback() -> None:
    class CE:
        def get_cost_and_usage_with_resources(self, **kw: Any) -> dict[str, Any]:
            assert kw["GroupBy"] == [{"Type": "DIMENSION", "Key": "RESOURCE_ID"}]
            return {
                "ResultsByTime": [
                    {"Groups": [{"Keys": ["i-9"], "Metrics": {"AmortizedCost": {"Amount": "7.5"}}}]}
                ]
                * 2
            }

    assert ce_fallback.fetch(CE(), date(2026, 11, 10)) == {"i-9": 15.0}


def test_hourly_capture_from_cur(db: Database) -> None:
    from costlens.clouds.aws import cur

    org_id, _ = make_org(db, "Acme", "o@acme.com")
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    rows = [
        {
            "line_item_usage_start_date": now - timedelta(hours=h),
            "line_item_line_item_type": "Usage",
            "line_item_usage_account_id": "1",
            "product": {"product_name": "EC2"},
            "line_item_unblended_cost": "2",
            "line_item_currency_code": "USD",
            "resource_tags": {},
        }
        for h in range(3)
    ]
    hourly: dict = {}
    cur.aggregate(iter([rows]), hourly, now - timedelta(days=1))
    assert len(hourly) == 3
    with db.session() as s:
        assert cur.store_hourly(s, org_id, "aws_cur:x", now.date().replace(day=1), hourly) == 3
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        assert len(s.scalars(select(CostHourly)).all()) == 3


def test_hourly_api(client: TestClient, db: Database) -> None:
    import uuid

    org_id, owner = make_org(db, "Acme", "o@acme.com")
    base = datetime(2026, 11, 10, 0, tzinfo=UTC)
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        for h in range(10):
            s.add(
                CostHourly(
                    id=uuid.uuid4(),
                    org_id=org_id,
                    hour=base + timedelta(hours=h),
                    source_key="x",
                    sub_account_id="1",
                    service_name="EC2",
                    billing_currency="USD",
                    effective_cost=Decimal("1.5"),
                )
            )
    c = sign_in_client(client, db, owner, org_id)
    r = c.get("/api/v1/costs/hourly", params={"hours": 6}).json()
    assert len(r["hours"]) == 6 and r["series"][0]["values"] == [1.5] * 6


def test_demo_inventory(db: Database) -> None:
    from costlens.demo.seed import ensure_demo, load_all_seeders, seeders

    load_all_seeders()
    if "inventory.aws" not in seeders():
        pytest.fail("demo seeders not registered")
    with db.session() as s:
        org_id = ensure_demo(s).id
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        states = {r.resource_type + ":" + (r.state or "") for r in s.scalars(select(CloudResource))}
    assert "ebs_volume:available" in states and "elastic_ip:unattached" in states


def test_collector_functions_directly() -> None:
    assert [f.resource_id for f in collectors.elastic_ips(FakeEC2(), "r")] == [
        "eipalloc-1",
        "eipalloc-2",
    ]
