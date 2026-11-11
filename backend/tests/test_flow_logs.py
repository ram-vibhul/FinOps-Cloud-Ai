from __future__ import annotations

import gzip
import io
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ccradar.accounts.roles import Role
from ccradar.costs import flow_logs as fl
from ccradar.costs.ingest import CostLine, replace_billing_period
from ccradar.db import Database
from ccradar.db.base import utcnow
from ccradar.db.tenant import set_tenant
from ccradar.inventory.aws import Found
from ccradar.inventory.service import upsert
from ccradar.k8s.service import ingest_objects
from ccradar.models import CloudConnection, K8sCluster, NetworkAttribution
from tests.fakes.aws import FakeAws
from tests.helpers import add_to_org, make_org, sign_in_client

ROLE = "arn:aws:iam::111111111111:role/ccradar"
ENIS = [
    {
        "NetworkInterfaceId": "eni-web",
        "AvailabilityZone": "ap-south-1a",
        "InterfaceType": "interface",
        "Attachment": {"InstanceId": "i-web"},
        "PrivateIpAddresses": [
            {"PrivateIpAddress": "10.0.1.10"},
            {"PrivateIpAddress": "10.0.1.50"},
        ],
    },
    {
        "NetworkInterfaceId": "eni-db",
        "AvailabilityZone": "ap-south-1b",
        "InterfaceType": "interface",
        "Description": "RDSNetworkInterface",
        "PrivateIpAddresses": [{"PrivateIpAddress": "10.0.2.20"}],
    },
    {
        "NetworkInterfaceId": "eni-nat",
        "AvailabilityZone": "ap-south-1a",
        "InterfaceType": "nat_gateway",
        "Description": "Interface for NAT Gateway nat-1",
        "PrivateIpAddresses": [
            {"PrivateIpAddress": "10.0.0.5", "Association": {"PublicIp": "13.0.0.1"}}
        ],
    },
]
HEADER = (
    "version account-id interface-id srcaddr dstaddr srcport dstport protocol packets bytes "
    "start end action log-status"
)
LINES = [
    HEADER,
    "2 111 eni-nat 10.0.1.50 52.95.1.1 443 51000 6 10 4000000000 1 2 ACCEPT OK",  # pod via NAT
    "2 111 eni-nat 52.95.1.1 10.0.1.10 443 51000 6 10 1000000000 1 2 ACCEPT OK",  # reply
    "2 111 eni-web 10.0.1.10 10.0.2.20 1 5432 6 10 3000000000 1 2 ACCEPT OK",  # sender
    "2 111 eni-db 10.0.1.10 10.0.2.20 1 5432 6 10 3000000000 1 2 ACCEPT OK",  # receiver
    "2 111 eni-web 10.0.1.10 8.8.8.8 1 53 17 1 2000000000 1 2 ACCEPT OK",  # internet
    "2 111 eni-web 10.0.1.10 8.8.4.4 1 53 17 1 999 1 2 REJECT OK",  # rejected: nothing
    "2 111 eni-web - - - - - - - 1 2 - NODATA",  # no data
    "2 111 eni-web 10.0.1.10 10.0.1.50 1 2 6 1 500 1 2 ACCEPT OK",  # same AZ: free
]


def test_parse_default_custom_and_skips() -> None:
    flows = list(fl.parse(LINES))
    assert len(flows) == 7 and flows[0].interface == "eni-nat" and flows[0].bytes == 4_000_000_000
    custom = list(
        fl.parse(
            [
                "interface-id pkt-srcaddr pkt-dstaddr srcaddr dstaddr bytes action",
                "eni-1 10.1.1.1 1.1.1.1 10.9.9.9 1.1.1.1 77 ACCEPT",
            ]
        )
    )
    assert custom[0].src == "10.1.1.1" and custom[0].bytes == 77  # the packet's own source wins
    default = list(fl.parse(["2 111 eni-1 10.0.0.1 10.0.0.2 1 2 6 1 9 1 2 ACCEPT OK"]))
    assert default[0].dst == "10.0.0.2"


def _book() -> fl.IpBook:
    inst = type("R", (), {"name": "checkout-web", "tags": {"Team": "payments"}})()
    book = fl.IpBook()
    book.add_interfaces(ENIS, "ap-south-1", {"i-web": inst})  # type: ignore[dict-item]
    book.add_pods([("10.0.1.50", "prod", "search", "indexer")])
    return book


def test_classification() -> None:
    book = _book()
    shares = fl.attribute(fl.parse(LINES), book)
    nat = {k: v["bytes"] for k, v in shares["nat_processing"].items()}
    assert nat == {"k8s:prod/search/indexer": 4_000_000_000, "ec2:i-web": 1_000_000_000}
    assert {k: v["bytes"] for k, v in shares["cross_az"].items()} == {
        "ec2:i-web": 3_000_000_000,
        "eni:RDSNetworkInterface": 3_000_000_000,
    }  # both sides pay
    assert {k: v["bytes"] for k, v in shares["internet_out"].items()} == {
        "ec2:i-web": 2_000_000_000
    }
    assert shares["nat_processing"]["ec2:i-web"]["owner"].team == "payments"
    assert "inter_region" not in shares


def test_discover_and_policy() -> None:
    class Ec2:
        def describe_flow_logs(self) -> dict[str, Any]:
            return {
                "FlowLogs": [
                    {
                        "LogDestinationType": "s3",
                        "LogDestination": "arn:aws:s3:::logs-bucket/flow",
                        "ResourceId": "vpc-1",
                    },
                    {
                        "LogDestinationType": "s3",
                        "LogDestination": "arn:aws:s3:::logs-bucket/flow",
                        "ResourceId": "vpc-2",
                    },
                    {
                        "LogDestinationType": "cloud-watch-logs",
                        "LogGroupName": "/vpc/flow",
                        "ResourceId": "vpc-3",
                    },
                ]
            }

    sources = fl.discover(Ec2())
    assert len(sources) == 2  # one entry per destination, whichever VPCs log to it
    assert {(x["type"], x.get("bucket"), x.get("prefix")) for x in sources} == {
        ("s3", "logs-bucket", "flow"),
        ("cloudwatch", None, None),
    }
    statements = fl.policy_statements(sources)
    assert statements[0]["Resource"] == ["arn:aws:s3:::logs-bucket", "arn:aws:s3:::logs-bucket/*"]
    assert statements[1]["Action"] == ["logs:FilterLogEvents"]


class FakeS3:
    def __init__(self) -> None:
        self.prefixes: list[str] = []

    def list_objects_v2(self, Bucket: str, Prefix: str, **kw: Any) -> dict[str, Any]:
        self.prefixes.append(Prefix)
        return (
            {"Contents": [{"Key": Prefix + "a.log.gz"}]}
            if "ap-south-1" in Prefix
            else {"Contents": []}
        )

    def download_fileobj(self, Bucket: str, Key: str, Fileobj: io.BytesIO) -> None:
        Fileobj.write(gzip.compress("\n".join(LINES).encode()))


class FakeEc2:
    def describe_network_interfaces(self, **kw: Any) -> dict[str, Any]:
        return {"NetworkInterfaces": ENIS}


def _setup(db: Database):
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    today = utcnow().date()
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        conn = CloudConnection(
            org_id=org_id,
            provider="aws",
            name="Prod",
            status="active",
            external_id="x",
            role_arn=ROLE,
            account_id="111111111111",
        )
        s.add(conn)
        upsert(
            s,
            org_id,
            "111111111111",
            [
                Found(
                    "EC2",
                    "ec2_instance",
                    "i-web",
                    "ap-south-1",
                    name="checkout-web",
                    state="running",
                    tags={"team": "payments"},
                )
            ],
        )
        cluster = K8sCluster(
            id=uuid.uuid4(), org_id=org_id, name="prod", token_hash="x" * 64, token_hint="x"
        )
        s.add(cluster)
        s.flush()
        ingest_objects(
            s,
            cluster,
            "Pod",
            "snap",
            [
                {
                    "uid": "p1",
                    "name": "indexer-5f-abc",
                    "namespace": "search",
                    "data": {
                        "podIP": "10.0.1.50",
                        "owners": [{"kind": "ReplicaSet", "name": "indexer-5f", "uid": "rs"}],
                    },
                }
            ],
            final=True,
        )
        conn_id = conn.id
    lines = [
        CostLine(
            charge_date=today - timedelta(days=d),
            provider="aws",
            service_name="Amazon EC2",
            region="ap-south-1",
            sub_account_id="111111111111",
            usage_type=usage,
            billed_cost=Decimal(c),
            effective_cost=Decimal(c),
            billing_currency="USD",
        )
        for d in range(1, 31)
        for usage, c in (
            ("APS3-NatGateway-Bytes", "10"),
            ("APS3-DataTransfer-Regional-Bytes", "4"),
            ("APS3-DataTransfer-Out-Bytes", "2"),
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
    aws = FakeAws(clients={"ec2": FakeEc2(), "s3": FakeS3()})
    aws.allow(ROLE, "x")
    return org_id, owner, conn_id, aws


def test_collect_and_api(client: TestClient, db: Database) -> None:
    org_id, owner, conn_id, aws = _setup(db)
    client.app.state.aws = aws  # type: ignore[attr-defined]
    member = add_to_org(db, org_id, "m@acme.com", Role.MEMBER)
    m = sign_in_client(client, db, member, org_id)
    assert (
        m.put(
            f"/api/v1/connections/{conn_id}/flow-logs", json={"enabled": True, "sources": []}
        ).status_code
        == 403
    )
    o = sign_in_client(client, db, owner, org_id)
    assert o.post(f"/api/v1/connections/{conn_id}/flow-logs/run").json()["code"] == "not_enabled"
    assert (
        o.put(
            f"/api/v1/connections/{conn_id}/flow-logs",
            json={"enabled": True, "sources": [{"type": "s3"}]},
        ).status_code
        == 422
    )
    r = o.put(
        f"/api/v1/connections/{conn_id}/flow-logs",
        json={
            "enabled": True,
            "sources": [{"type": "s3", "bucket": "logs-bucket", "prefix": "flow"}],
        },
    )
    assert r.json()["policy_statements"][0]["Sid"] == "ReadVpcFlowLogs"
    run = o.post(f"/api/v1/connections/{conn_id}/flow-logs/run").json()
    assert run["lines"] == len(LINES) and run["errors"] == {}
    yesterday = (datetime.now(UTC) - timedelta(days=1)).date()
    assert (
        f"flow/AWSLogs/111111111111/vpcflowlogs/ap-south-1/{yesterday:%Y/%m/%d}/"
        in aws.clients["s3"].prefixes
    )
    data = m.get("/api/v1/costs/network/attribution").json()
    rows = {(i["category"], i["owner"]): i for i in data["items"]}
    month = 30  # the network view covers the last 30 days of the bill
    indexer = rows[("nat_processing", "search/indexer (prod)")]
    assert indexer["share_pct"] == 80.0 and indexer["cost_monthly"] == pytest.approx(
        10 * month * 0.8, abs=0.01
    )
    assert indexer["team"] == "search"
    web = rows[("internet_out", "checkout-web")]
    assert web["cost_monthly"] == pytest.approx(2 * month, abs=0.01) and web["team"] == "payments"
    assert {t["team"] for t in data["teams"]} >= {"search", "payments"}
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        assert len(s.scalars(select(NetworkAttribution)).all()) == len(data["items"])
        conn = s.get(CloudConnection, conn_id)
        assert conn is not None and conn.settings["flow_logs"]["lines"] == len(LINES)
