from __future__ import annotations

import gzip
import json
import uuid
from datetime import date
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from costlens.clouds.azure import advisor
from costlens.clouds.azure import costs as azure_costs
from costlens.clouds.azure.client import AzureClient, AzureError
from costlens.db import Database
from costlens.db.tenant import set_tenant
from costlens.models import CloudConnection, CostLineItem, ExternalRecommendation
from tests.helpers import make_org, sign_in_client

TENANT = "11111111-1111-1111-1111-111111111111"
CLIENT = "22222222-2222-2222-2222-222222222222"
SUB = "33333333-3333-3333-3333-333333333333"
VM = f"/subscriptions/{SUB}/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/vm1"
RIGHTSIZE = "Right-size or shutdown underutilized virtual machines"
FOCUS_CSV = (
    "ChargePeriodStart,ChargePeriodEnd,BilledCost,EffectiveCost,BillingCurrency,ServiceName,"
    "ProviderName,RegionId,ResourceId,SubAccountId,ChargeCategory,Tags\n"
    "2026-10-01T00:00:00Z,2026-10-02T00:00:00Z,12.50,10.00,USD,Virtual Machines,Microsoft,"
    'westeurope,/subscriptions/s/vm1,s,Usage,"{""team"":""web""}"\n'
    "2026-10-02T00:00:00Z,2026-10-03T00:00:00Z,7.50,7.50,USD,Storage,Microsoft,westeurope,"
    "/subscriptions/s/disk1,s,Usage,{}\n"
)
BLOB_LIST = """<?xml version="1.0" encoding="utf-8"?>
<EnumerationResults><Blobs>
<Blob><Name>exports/focus/20261001-20261031/run-old/part_0_0001.csv.gz</Name>
<Properties><Last-Modified>Mon, 02 Nov 2026 01:00:00 GMT</Last-Modified></Properties></Blob>
<Blob><Name>exports/focus/20261001-20261031/run-new/part_0_0001.csv.gz</Name>
<Properties><Last-Modified>Sun, 08 Nov 2026 01:00:00 GMT</Last-Modified></Properties></Blob>
<Blob><Name>exports/focus/20261001-20261031/run-new/manifest.json</Name>
<Properties><Last-Modified>Sun, 08 Nov 2026 01:00:00 GMT</Last-Modified></Properties></Blob>
</Blobs><NextMarker/></EnumerationResults>"""


class FakeAzure:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.sign_in_ok = True
        self.throttle = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = request.url
        if url.host == "login.microsoftonline.com":
            if not self.sign_in_ok:
                return httpx.Response(401, json={"error": "invalid_client"})
            scope = parse_qs(request.content.decode())["scope"][0]
            return httpx.Response(200, json={"access_token": f"tok:{scope}", "expires_in": 3600})
        if url.host.endswith(".blob.core.windows.net"):
            assert (
                request.headers["authorization"] == "Bearer tok:https://storage.azure.com/.default"
            )
            if url.params.get("comp") == "list":
                return httpx.Response(200, content=BLOB_LIST.encode())
            if "run-old" in url.path:
                return httpx.Response(200, content=gzip.compress(b"ChargePeriodStart,BilledCost\n"))
            return httpx.Response(200, content=gzip.compress(FOCUS_CSV.encode()))
        assert (
            request.headers["authorization"] == "Bearer tok:https://management.azure.com/.default"
        )
        if url.path.endswith("/providers/Microsoft.CostManagement/query"):
            if self.throttle:
                self.throttle -= 1
                return httpx.Response(429, headers={"retry-after": "1"})
            body = json.loads(request.content)
            amortized = body["type"] == "AmortizedCost"
            rows = [
                [40.0 if not amortized else 30.0, 20261001, SUB, "Virtual Machines", "USD"],
                [5.0, 20261002, SUB, "Storage", "USD"],
                [120.0 if not amortized else 0.0, 20261003, SUB, "Reservations", "USD"],
            ]
            return httpx.Response(
                200,
                json={
                    "properties": {
                        "columns": [
                            {"name": "Cost"},
                            {"name": "UsageDate"},
                            {"name": "SubscriptionId"},
                            {"name": "ServiceName"},
                            {"name": "Currency"},
                        ],
                        "rows": rows,
                        "nextLink": None,
                    }
                },
            )
        if url.path.endswith("/providers/Microsoft.Advisor/recommendations"):
            assert url.params["$filter"] == "Category eq 'Cost'"
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "name": "rec-1",
                            "id": f"{VM}/providers/Microsoft.Advisor/recommendations/rec-1",
                            "properties": {
                                "impact": "High",
                                "impactedField": "Microsoft.Compute/virtualMachines",
                                "shortDescription": {
                                    "problem": RIGHTSIZE,
                                    "solution": RIGHTSIZE,
                                },
                                "extendedProperties": {
                                    "savingsAmount": "61.5",
                                    "savingsCurrency": "USD",
                                    "currentSku": "Standard_D4s_v5",
                                    "targetSku": "Standard_D2s_v5",
                                    "region": "westeurope",
                                },
                                "resourceMetadata": {"resourceId": VM},
                            },
                        },
                        {
                            "name": "rec-2",
                            "id": "x",
                            "properties": {
                                "impact": "Medium",
                                "shortDescription": {
                                    "solution": "Buy reserved instances to save money"
                                },
                                "extendedProperties": {
                                    "annualSavingsAmount": "1200",
                                    "savingsCurrency": "USD",
                                },
                            },
                        },
                    ]
                },
            )
        return httpx.Response(404, json={"error": {"code": "NotFound", "message": str(url)}})


def make_client(fake: FakeAzure) -> AzureClient:
    c = AzureClient(
        httpx.Client(transport=httpx.MockTransport(fake)), TENANT, CLIENT, "s3cret-value"
    )
    c._fast = True
    return c


def test_query_import_actual_and_amortized() -> None:
    fake = FakeAzure()
    fake.throttle = 1  # one 429 is retried
    lines = azure_costs.fetch_period(
        make_client(fake), f"/subscriptions/{SUB}", date(2026, 10, 1), date(2026, 11, 1)
    )
    by = {(x.charge_date, x.service_name): x for x in lines}
    vm = by[(date(2026, 10, 1), "Virtual Machines")]
    assert vm.billed_cost == 40 and vm.effective_cost == 30 and vm.provider == "azure"
    assert vm.sub_account_id == SUB and vm.charge_category == "Usage"
    ri = by[(date(2026, 10, 3), "Reservations")]
    assert ri.charge_category == "Purchase" and ri.pricing_category == "Committed"
    body = json.loads(next(r for r in fake.requests if r.url.path.endswith("/query")).content)
    assert body["timePeriod"] == {"from": "2026-10-01T00:00:00Z", "to": "2026-10-31T23:59:59Z"}
    assert [g["name"] for g in body["dataset"]["grouping"]] == ["SubscriptionId", "ServiceName"]
    tokens = [r for r in fake.requests if r.url.host == "login.microsoftonline.com"]
    assert len(tokens) == 1  # cached


def test_focus_export_uses_newest_run() -> None:
    fake = FakeAzure()
    cfg = {"storage_account": "costexports", "container": "billing", "prefix": "exports/focus"}
    lines = azure_costs.focus_period(make_client(fake), cfg, date(2026, 10, 1))
    assert lines is not None and len(lines) == 2
    assert {x.provider for x in lines} == {"azure"}
    vm = next(x for x in lines if x.service_name == "Virtual Machines")
    assert (
        vm.billed_cost == pytest.approx(12.5)
        and vm.region == "westeurope"
        and vm.tags == {"team": "web"}
    )
    assert not any("run-old" in r.url.path for r in fake.requests)
    assert azure_costs.focus_period(make_client(fake), cfg, date(2026, 9, 1)) is None


def test_client_refuses_other_hosts_and_reports_errors() -> None:
    fake = FakeAzure()
    c = make_client(fake)
    with pytest.raises(AzureError, match="only Azure Resource Manager"):
        c.request("GET", "https://evil.example/subscriptions")
    with pytest.raises(AzureError, match="not a storage account"):
        c.blob("Bad.Name", "x")
    with pytest.raises(AzureError) as err:
        c.request("GET", "/nothing-here")
    assert err.value.status == 404 and "NotFound" in str(err.value)
    fake.sign_in_ok = False
    with pytest.raises(AzureError, match="sign-in failed: invalid_client"):
        make_client(fake).token()


def test_connect_import_and_advisor(client: TestClient, db: Database) -> None:
    fake = FakeAzure()
    client.app.state.http = httpx.Client(transport=httpx.MockTransport(fake))  # type: ignore[attr-defined]
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    c = sign_in_client(client, db, owner, org_id)
    base = {
        "name": "Azure prod",
        "tenant_id": TENANT,
        "client_id": CLIENT,
        "client_secret": "s3cret-value",
    }
    assert (
        c.post("/api/v1/connections/azure", json={**base, "scope": "/foo"}).json()["code"]
        == "bad_scope"
    )
    assert (
        c.post(
            "/api/v1/connections/azure",
            json={**base, "scope": f"/subscriptions/{SUB}", "storage_account": "abc"},
        ).json()["code"]
        == "bad_export"
    )
    fake.sign_in_ok = False
    r = c.post("/api/v1/connections/azure", json={**base, "scope": f"subscriptions/{SUB}"})
    assert r.json()["code"] == "azure_sign_in_failed"
    fake.sign_in_ok = True
    r = c.post("/api/v1/connections/azure", json={**base, "scope": f"subscriptions/{SUB}"})
    assert r.status_code == 201, r.text
    conn = r.json()
    assert conn["provider"] == "azure" and conn["status"] == "active" and conn["account_id"] == SUB
    assert conn["azure"] == {
        "tenant_id": TENANT,
        "client_id": CLIENT,
        "scope": f"/subscriptions/{SUB}",
    }
    assert "s3cret" not in r.text
    listed = c.get("/api/v1/connections").json()
    assert listed[0]["azure"]["scope"] == f"/subscriptions/{SUB}" and "s3cret" not in json.dumps(
        listed
    )
    # The create call queued the first import; run the importer and Advisor directly.
    with db.session() as s:
        set_tenant(s, bypass=True)
        stored = s.get(CloudConnection, uuid.UUID(conn["id"]))
        assert stored is not None and stored.secret == "s3cret-value"
        cl = make_client(fake)
        runs = azure_costs.import_connection(s, cl, stored.id, months=1, today=date(2026, 10, 20))
        assert [r.rows for r in runs] == [3]
        out = advisor.collect(s, cl, stored.id)
        assert out == {"count": 2, "errors": {}}
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        assert sum(float(x.billed_cost) for x in s.scalars(select(CostLineItem))) == 165.0
        recs = {r.source_id: r for r in s.scalars(select(ExternalRecommendation))}
        assert recs["rec-1"].action == "rightsize" and recs["rec-1"].savings_monthly == 61.5
        assert (recs["rec-1"].resource_id or "").endswith("/virtualMachines/vm1")
        assert recs["rec-2"].action == "purchase" and recs["rec-2"].savings_monthly == 100.0
    imp = c.post(f"/api/v1/connections/{conn['id']}/import", json={"months": 2}).json()
    assert imp == {"status": "queued", "source": "azure"}


def test_imported_azure_advice_becomes_an_azure_finding(db: Database) -> None:
    from costlens.findings import service

    org_id, _ = make_org(db, "Acme", "o@acme.com")
    fake = FakeAzure()
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        conn = CloudConnection(
            org_id=org_id,
            provider="azure",
            name="az",
            status="active",
            external_id="azure-x",
            secret="s",
            settings={"tenant_id": TENANT, "client_id": CLIENT, "scope": f"/subscriptions/{SUB}"},
        )
        s.add(conn)
        s.flush()
        advisor.collect(s, make_client(fake), conn.id)
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        service.run(s, org_id, only=["aws.imported_advice"])
        from costlens.models import Finding

        found = {f.resource_id: f for f in s.scalars(select(Finding))}
    vm = next(f for rid, f in found.items() if rid and rid.endswith("/vm1"))
    assert vm.provider == "azure" and "Azure console" in vm.description
    assert "Azure Advisor" in vm.description


def test_payload_shape_helpers() -> None:
    assert advisor._action("Buy reserved instances") == "purchase"
    assert advisor._action("Delete unattached disks") == "delete"
    assert advisor._action("Right-size") == "rightsize"
    assert advisor._action("Something") == "other"
