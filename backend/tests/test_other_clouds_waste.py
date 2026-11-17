"""Azure and Google Cloud inventory and waste detectors (11.28), against fakes."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from ccradar.costs.ingest import CostLine, replace_billing_period
from ccradar.db import Database
from ccradar.db.tenant import set_tenant
from ccradar.findings import other_clouds as detectors
from ccradar.inventory import other_clouds
from ccradar.models import CloudConnection, CloudResource
from tests.helpers import make_org

SUB = "11111111-2222-3333-4444-555555555555"
DISK = f"/subscriptions/{SUB}/resourceGroups/RG/providers/Microsoft.Compute/disks/old-data"
NOW = datetime(2026, 11, 12, tzinfo=UTC)


class Response:
    def __init__(self, body: dict) -> None:
        self.body = body

    def json(self) -> dict:
        return self.body


class FakeAzure:
    def __init__(self, with_disk: bool = True) -> None:
        self.with_disk = with_disk

    def request(self, method: str, url: str, **_: Any) -> Response:
        if "Microsoft.Compute/disks" in url:
            disks = [
                {
                    "id": DISK,
                    "name": "old-data",
                    "location": "centralindia",
                    "sku": {"name": "Premium_LRS"},
                    "properties": {"diskSizeGB": 256, "diskState": "Unattached"},
                },
                {
                    "id": DISK.replace("old-data", "os"),
                    "name": "os",
                    "location": "centralindia",
                    "sku": {"name": "Premium_LRS"},
                    "properties": {"diskSizeGB": 128, "diskState": "Attached"},
                },
            ]
            return Response({"value": disks if self.with_disk else disks[1:]})
        if "publicIPAddresses" in url:
            return Response(
                {
                    "value": [
                        {
                            "id": f"/subscriptions/{SUB}/x/publicIPAddresses/spare",
                            "name": "spare",
                            "location": "centralindia",
                            "sku": {"name": "Standard"},
                            "properties": {"ipAddress": "20.1.2.3"},
                        },
                        {
                            "id": f"/subscriptions/{SUB}/x/publicIPAddresses/web",
                            "name": "web",
                            "location": "centralindia",
                            "sku": {"name": "Standard"},
                            "properties": {"ipConfiguration": {"id": "nic"}},
                        },
                    ]
                }
            )
        raise AssertionError(url)


class FakeGcp:
    def request(self, method: str, url: str, **_: Any) -> dict:
        if url.endswith("/aggregated/disks"):
            return {
                "items": {
                    "zones/asia-south1-a": {
                        "disks": [
                            {
                                "selfLink": "https://compute/projects/shop-prod/zones/asia-south1-a/disks/scratch",
                                "name": "scratch",
                                "sizeGb": "500",
                                "type": "https://x/diskTypes/pd-ssd",
                                "zone": "https://x/zones/asia-south1-a",
                                "users": [],
                            },
                            {
                                "selfLink": "https://compute/projects/shop-prod/zones/asia-south1-a/disks/boot",
                                "name": "boot",
                                "sizeGb": "10",
                                "type": "https://x/diskTypes/pd-balanced",
                                "zone": "https://x/zones/asia-south1-a",
                                "users": ["vm"],
                            },
                        ]
                    },
                    "zones/us-east1-b": {"warning": {"code": "NO_RESULTS_ON_PAGE"}},
                }
            }
        if url.endswith("/aggregated/addresses"):
            return {
                "items": {
                    "regions/asia-south1": {
                        "addresses": [
                            {
                                "selfLink": "https://compute/projects/shop-prod/regions/asia-south1/addresses/old-lb",
                                "name": "old-lb",
                                "address": "34.1.1.1",
                                "status": "RESERVED",
                                "addressType": "EXTERNAL",
                                "region": "https://x/regions/asia-south1",
                            },
                            {
                                "selfLink": "https://compute/x/addresses/internal",
                                "name": "internal",
                                "status": "RESERVED",
                                "addressType": "INTERNAL",
                                "region": "https://x/regions/asia-south1",
                            },
                        ]
                    }
                }
            }
        raise AssertionError(url)


def connection(db: Database, org_id, provider: str):
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        c = CloudConnection(
            org_id=org_id,
            provider=provider,
            name=provider,
            status="active",
            external_id=provider,
            settings={"scope": f"/subscriptions/{SUB}"},
        )
        s.add(c)
        s.flush()
        return c.id


def test_azure_unattached_disks_and_ips(db: Database) -> None:
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    conn = connection(db, org_id, "azure")
    with db.session() as s:  # the bill names the disk (in lower case): its own cost wins
        replace_billing_period(
            s,
            org_id=org_id,
            source_kind="t",
            source_key="t",
            billing_period=date(2026, 11, 1),
            lines=[
                CostLine(
                    charge_date=date(2026, 11, 1) + timedelta(days=i),
                    provider="azure",
                    service_name="Storage",
                    resource_id=DISK.lower(),
                    billed_cost=Decimal(1),
                    effective_cost=Decimal(1),
                    billing_currency="USD",
                )
                for i in range(30)
            ],
        )
    with db.session() as s:
        assert other_clouds.collect(s, FakeAzure(), conn, now=NOW) == {SUB: 4}
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        disks = detectors.azure_disks(s, org_id, "USD")
        ips = detectors.azure_ips(s, org_id, "USD")
    (disk,) = disks
    assert disk.evidence["basis"] == "billing" and 29 < disk.savings_monthly < 32
    assert disk.commands[-1].startswith("az disk delete --ids")
    (ip,) = ips
    assert ip.savings_monthly == 3.65 and ip.evidence["basis"] == "list price"
    with db.session() as s:  # deleted since: marked gone, no finding
        other_clouds.collect(s, FakeAzure(with_disk=False), conn, now=NOW + timedelta(days=1))
        set_tenant(s, org_id=org_id)
        state = s.scalar(
            select(CloudResource.state).where(CloudResource.resource_id == DISK.lower())
        )
        assert state == "deleted" and detectors.azure_disks(s, org_id, "USD") == []


def test_gcp_unattached_disks_and_reserved_addresses(db: Database) -> None:
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    with db.session() as s:
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
    with db.session() as s:
        assert other_clouds.collect(s, FakeGcp(), conn, now=NOW) == {
            "shop-prod": 3
        }  # internal left out
        set_tenant(s, org_id=org_id)
        (disk,) = detectors.gcp_disks(s, org_id, "USD")
        (ip,) = detectors.gcp_ips(s, org_id, "USD")
    assert disk.savings_monthly == 85.0 and disk.region == "asia-south1"
    assert "--zone asia-south1-a" in disk.commands[-1]
    assert ip.savings_monthly == 7.3 and "--region asia-south1" in ip.commands[0]
