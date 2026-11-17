"""Live Radar for Azure and Google Cloud (10.2), against fakes."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ccradar.changes import other_clouds
from ccradar.costs.ingest import CostLine, replace_billing_period
from ccradar.db import Database
from ccradar.db.tenant import set_tenant
from ccradar.models import ChangeEvent, CloudConnection
from tests.helpers import make_org, sign_in_client

NOW = datetime(2026, 11, 12, 10, 0, tzinfo=UTC)
SUB = "11111111-2222-3333-4444-555555555555"
API3 = "projects/shop-prod/zones/asia-south1-a/instances/api-3"
VM = f"/subscriptions/{SUB}/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/web1"


class Response:
    def __init__(self, body: dict) -> None:
        self.body = body

    def json(self) -> dict:
        return self.body


class FakeAzure:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def request(self, method: str, url: str, **_: Any) -> Response:
        self.calls.append(url)
        if url.startswith("/subscriptions?"):
            return Response({"value": [{"subscriptionId": SUB}]})
        if "eventtypes/management/values" in url:

            def event(i: str, op: str, status: str = "Succeeded", rid: str = VM) -> dict:
                return {
                    "eventDataId": i,
                    "caller": "asha@acme.com",
                    "eventTimestamp": "2026-11-12T09:30:00Z",
                    "operationName": {"value": op},
                    "status": {"value": status},
                    "resourceId": rid,
                    "resourceProviderName": {"value": "Microsoft.Compute"},
                }

            return Response(
                {
                    "value": [
                        event("a1", "Microsoft.Compute/virtualMachines/write"),
                        event("a2", "Microsoft.Compute/virtualMachines/write", status="Started"),
                        event(
                            "a3",
                            "Microsoft.Compute/virtualMachines/delete",
                            rid=VM.replace("web1", "old"),
                        ),
                        event("a4", "Microsoft.Storage/storageAccounts/listKeys/action"),
                    ]
                }
            )
        if url.startswith(VM):
            return Response(
                {
                    "location": "centralindia",
                    "properties": {
                        "hardwareProfile": {"vmSize": "Standard_D4s_v5"},
                        "timeCreated": "2026-11-12T09:29:10Z",
                    },
                }
            )
        raise AssertionError(url)


def retail(request: httpx.Request) -> httpx.Response:
    assert request.url.host == "prices.azure.com"
    assert "armSkuName eq 'Standard_D4s_v5'" in request.url.params["$filter"]
    return httpx.Response(
        200,
        json={
            "Items": [
                {
                    "retailPrice": 0.21,
                    "unitOfMeasure": "1 Hour",
                    "productName": "Virtual Machines Dsv5 Series",
                    "skuName": "D4s v5",
                },
                {
                    "retailPrice": 0.39,
                    "unitOfMeasure": "1 Hour",
                    "productName": "Virtual Machines Dsv5 Series Windows",
                    "skuName": "D4s v5",
                },
                {
                    "retailPrice": 0.04,
                    "unitOfMeasure": "1 Hour",
                    "productName": "Virtual Machines Dsv5 Series",
                    "skuName": "D4s v5 Spot",
                },
            ]
        },
    )


class FakeGcp:
    def __init__(self) -> None:
        self.bodies: list[dict] = []

    def request(self, method: str, url: str, **kw: Any) -> dict:
        assert url == "https://logging.googleapis.com/v2/entries:list"
        self.bodies.append(kw["json"])
        if len(self.bodies) == 1:
            return {
                "entries": [
                    {
                        "insertId": "g1",
                        "timestamp": "2026-11-12T09:40:00Z",
                        "operation": {"first": True},
                        "protoPayload": {
                            "methodName": "v1.compute.instances.insert",
                            "resourceName": API3,
                            "authenticationInfo": {
                                "principalEmail": "ci@shop-prod.iam.gserviceaccount.com"
                            },
                            "request": {
                                "machineType": "zones/asia-south1-a/machineTypes/n2-standard-8"
                            },
                        },
                    },
                    {
                        "insertId": "g2",
                        "timestamp": "2026-11-12T09:41:00Z",
                        "operation": {"last": True},
                        "protoPayload": {
                            "methodName": "v1.compute.instances.insert",
                            "resourceName": API3,
                        },
                    },
                ],
                "nextPageToken": "p2",
            }
        return {
            "entries": [
                {
                    "insertId": "g3",
                    "timestamp": "2026-11-12T09:50:00Z",
                    "protoPayload": {
                        "methodName": "v1.compute.instances.delete",
                        "status": {"code": 7},
                        "resourceName": "projects/shop-prod/zones/asia-south1-a/instances/x",
                    },
                }
            ]
        }


def connection(db: Database, org_id, provider: str) -> Any:
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        c = CloudConnection(
            org_id=org_id,
            provider=provider,
            name=provider,
            status="active",
            external_id=f"{provider}-1",
            settings={"scope": f"/subscriptions/{SUB}"},
        )
        s.add(c)
        s.flush()
        return c.id


def test_azure_activity_log_prices_new_vms(client: TestClient, db: Database) -> None:
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    conn = connection(db, org_id, "azure")
    http = httpx.Client(transport=httpx.MockTransport(retail))
    other_clouds._azure_prices.clear()
    with db.session() as s:
        n = other_clouds.import_connection(s, conn, azure_client=FakeAzure(), http=http, now=NOW)
    assert n == 2  # the write (succeeded) and the delete; "Started" and listKeys ignored
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        by_name = {e.attributes["event_name"]: e for e in s.scalars(select(ChangeEvent))}
    vm = by_name["microsoft.compute/virtualmachines/write"]
    assert vm.attributes["impact"] == {
        "monthly": round(0.21 * 730, 2),
        "currency": "USD",
        "source": "list price",
        "label": "1 x Standard_D4s_v5",
    }
    assert vm.actor == "asha@acme.com" and vm.account == SUB
    assert "impact" not in by_name["microsoft.compute/virtualmachines/delete"].attributes
    with db.session() as s:  # the same window again: nothing new
        assert (
            other_clouds.import_connection(s, conn, azure_client=FakeAzure(), http=http, now=NOW)
            == 0
        )
    feed = (
        sign_in_client(client, db, owner, org_id).get("/api/v1/radar", params={"hours": 720}).json()
    )
    assert feed["items"][0]["label"] == "1 x Standard_D4s_v5"
    assert feed["scans"][0] == {
        "connection": "azure",
        "at": NOW.isoformat(),
        "complete": True,
        "problems": [],
    }


def test_gcp_audit_logs_price_instances_from_machine_type(db: Database) -> None:
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    with db.session() as s:  # the project comes from the bill
        replace_billing_period(
            s,
            org_id=org_id,
            source_kind="t",
            source_key="t",
            billing_period=date(2026, 11, 1),
            lines=[
                CostLine(
                    charge_date=date(2026, 11, 10),
                    provider="gcp",
                    service_name="Compute Engine",
                    sub_account_id="shop-prod",
                    region="asia-south1",
                    billed_cost=Decimal(5),
                    effective_cost=Decimal(5),
                    billing_currency="USD",
                )
            ],
        )
    conn = connection(db, org_id, "gcp")
    fake = FakeGcp()
    with db.session() as s:
        assert other_clouds.import_connection(s, conn, gcp_client=fake, now=NOW) == 1
    assert fake.bodies[1]["pageToken"] == "p2" and "shop-prod" in fake.bodies[0]["filter"]
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        (e,) = s.scalars(select(ChangeEvent)).all()
    hourly = 0.031611 * 8 + 0.004237 * 32
    assert e.attributes["impact"]["monthly"] == round(hourly * 730, 2)
    assert e.region == "asia-south1" and (e.actor or "").startswith("ci@")


@pytest.mark.parametrize(
    ("machine", "hourly"),
    [
        ("e2-medium", 0.033503),
        ("n1-highmem-4", 0.031611 * 4 + 0.004237 * 26),
        ("e2-custom-4-8192", 0.021811 * 4 + 0.002923 * 8),
        ("a3-megagpu-8g", None),
    ],
)
def test_gcp_machine_types(machine: str, hourly: float | None) -> None:
    got = other_clouds.gcp_hourly(f"zones/x/machineTypes/{machine}")
    assert (round(got, 6) if got is not None else None) == (round(hourly, 6) if hourly else None)
