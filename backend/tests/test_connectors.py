from __future__ import annotations

import json
import uuid
from datetime import date
from decimal import Decimal

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import select

from costlens.connectors import saas
from costlens.db import Database
from costlens.db.tenant import set_tenant
from costlens.models import CloudConnection, CostLineItem
from tests.helpers import make_org, sign_in_client

_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = _key.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
).decode()
OCT = date(2026, 10, 1)
ORG = "5f1a2b3c4d5e6f7a8b9c0d1e"


class FakeVendors:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.deny = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url, host = request.url, request.url.host
        if self.deny:
            return httpx.Response(403, text="forbidden")
        if host.endswith(".snowflakecomputing.com"):
            token = request.headers["authorization"].removeprefix("Bearer ")
            claims = jwt.decode(token, _key.public_key(), algorithms=["RS256"])
            assert claims["sub"] == "XY12345.COSTLENS" and claims["iss"].startswith(
                "XY12345.COSTLENS.SHA256:"
            )
            assert request.headers["x-snowflake-authorization-token-type"] == "KEYPAIR_JWT"
            if request.method == "POST":
                body = json.loads(request.content)
                assert (
                    body["bindings"]["1"]["value"] == "2026-10-01"
                    and body["warehouse"] == "REPORTING"
                )
                return httpx.Response(202, json={"statementHandle": "h1"})
            if url.params.get("partition") == "1":
                return httpx.Response(
                    200, json={"data": [["2026-10-02", "PROD", "STORAGE", "12.5", "USD"]]}
                )
            return httpx.Response(
                200,
                json={
                    "statementHandle": "h1",
                    "resultSetMetaData": {"partitionInfo": [{}, {}]},
                    "data": [
                        ["2026-10-01", "PROD", "WAREHOUSE_METERING", "300.75", "USD"],
                        ["2026-10-01", "DEV", "AI_SERVICES", "0", "USD"],
                    ],
                },
            )
        if host.endswith(".cloud.databricks.com"):
            assert request.headers["authorization"] == "Bearer dapi-test"
            if request.method == "POST":
                body = json.loads(request.content)
                assert body["warehouse_id"] == "wh1" and "system.billing.usage" in body["statement"]
                return httpx.Response(
                    200, json={"statement_id": "s1", "status": {"state": "PENDING"}}
                )
            if url.path.endswith("/chunks/1"):
                return httpx.Response(
                    200,
                    json={
                        "data_array": [
                            [
                                "2026-10-03",
                                "123",
                                "PREMIUM_SQL_COMPUTE",
                                "SQL",
                                "wh-9",
                                "{}",
                                "4",
                                "DBU",
                                "2.8",
                                "USD",
                            ]
                        ]
                    },
                )
            cols = [
                "usage_date",
                "workspace_id",
                "sku_name",
                "billing_origin_product",
                "resource",
                "tags",
                "quantity",
                "unit",
                "cost",
                "currency",
            ]
            return httpx.Response(
                200,
                json={
                    "statement_id": "s1",
                    "status": {"state": "SUCCEEDED"},
                    "manifest": {"schema": {"columns": [{"name": c} for c in cols]}},
                    "result": {
                        "data_array": [
                            [
                                "2026-10-01",
                                "123",
                                "PREMIUM_JOBS_COMPUTE",
                                "JOBS",
                                "0101-abc",
                                '{"team":"data"}',
                                "10",
                                "DBU",
                                "1.5",
                                "USD",
                            ]
                        ],
                        "next_chunk_index": 1,
                    },
                },
            )
        if host == "api.datadoghq.eu":
            assert (
                request.headers["dd-api-key"] == "ddapi"
                and request.headers["dd-application-key"] == "ddapp"
            )
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "attributes": {
                                "org_name": "Acme",
                                "public_id": "abc",
                                "charges": [
                                    {
                                        "product_name": "infra_host",
                                        "charge_type": "committed",
                                        "cost": 100,
                                    },
                                    {
                                        "product_name": "infra_host",
                                        "charge_type": "total",
                                        "cost": 450.5,
                                    },
                                    {
                                        "product_name": "logs_indexed",
                                        "charge_type": "total",
                                        "cost": 99,
                                    },
                                ],
                            }
                        }
                    ]
                },
            )
        if host == "cloud.mongodb.com":
            item = {
                "startDate": "2026-10-04T00:00:00Z",
                "sku": "ATLAS_AWS_INSTANCE_M30",
                "clusterName": "prod",
                "groupId": "g1",
                "groupName": "Prod",
                "totalPriceCents": 1234,
                "quantity": 24,
                "unit": "server hours",
            }
            if url.path.endswith("/invoices"):
                return httpx.Response(
                    200,
                    json={
                        "results": [
                            {"id": "inv1", "startDate": "2026-10-01T00:00:00Z"},
                            {"id": "old", "startDate": "2026-06-01T00:00:00Z"},
                        ]
                    },
                )
            if url.path.endswith("/invoices/inv1"):
                return httpx.Response(
                    200, json={"lineItems": [item, {**item, "startDate": "2026-09-30T00:00:00Z"}]}
                )
            if url.path.endswith("/invoices/pending"):
                return httpx.Response(
                    200,
                    json={
                        "lineItems": [
                            item,
                            {**item, "startDate": "2026-10-05T00:00:00Z", "totalPriceCents": 1000},
                        ]
                    },
                )
        return httpx.Response(404, text=f"unexpected {url}")


def http(fake: FakeVendors) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(fake))


def test_snowflake_keypair_jwt_async_and_partitions() -> None:
    fake = FakeVendors()
    cfg = {
        "account": "xy12345.eu-west-1",
        "user": "costlens",
        "warehouse": "REPORTING",
        "_fast": True,
    }
    lines = saas.snowflake_month(http(fake), cfg, {"private_key": PEM}, OCT)
    assert [(x.charge_date, x.service_name, x.billed_cost) for x in lines] == [
        (date(2026, 10, 1), "Snowflake Warehouse Metering", Decimal("300.75")),
        (date(2026, 10, 2), "Snowflake Storage", Decimal("12.5")),
    ]
    assert fake.requests[0].url.host == "xy12345.eu-west-1.snowflakecomputing.com"
    with pytest.raises(saas.ConnectorError):
        saas.snowflake_month(
            http(fake), {"account": "evil.com/x", "user": "u"}, {"private_key": PEM}, OCT
        )


def test_databricks_statement_polling_and_chunks() -> None:
    fake = FakeVendors()
    cfg = {"host": "dbc-1234.cloud.databricks.com", "warehouse_id": "wh1", "_fast": True}
    lines = saas.databricks_month(http(fake), cfg, {"token": "dapi-test"}, OCT)
    jobs, sql = lines
    assert (
        jobs.service_name == "Databricks Jobs"
        and jobs.billed_cost == Decimal("1.5")
        and jobs.tags == {"team": "data"}
    )
    assert (
        jobs.resource_id == "0101-abc"
        and jobs.consumed_quantity == 10
        and jobs.consumed_unit == "DBU"
    )
    assert sql.charge_date == date(2026, 10, 3) and sql.sub_account_id == "123"
    for bad in ("evil.example.com", "x.cloud.databricks.com.evil.com"):
        with pytest.raises(saas.ConnectorError):
            saas.databricks_month(http(fake), {**cfg, "host": bad}, {"token": "t"}, OCT)


def test_datadog_monthly_totals_per_product() -> None:
    fake = FakeVendors()
    lines = saas.datadog_month(
        http(fake), {"site": "datadoghq.eu"}, {"api_key": "ddapi", "app_key": "ddapp"}, OCT
    )
    assert [(x.service_name, x.billed_cost, x.charge_date) for x in lines] == [
        ("Datadog Infra Host", Decimal("450.5"), OCT),
        ("Datadog Logs Indexed", Decimal("99"), OCT),
    ]
    assert fake.requests[0].url.path.endswith("/historical_cost")
    with pytest.raises(saas.ConnectorError, match="site"):
        saas.datadog_month(http(fake), {"site": "evil.com"}, {"api_key": "a", "app_key": "b"}, OCT)


def test_atlas_invoices_deduplicated_and_in_month() -> None:
    fake = FakeVendors()
    lines = saas.atlas_month(
        http(fake), {"org_id": ORG}, {"public_key": "pub", "private_key": "priv"}, OCT
    )
    assert sorted((x.charge_date, x.billed_cost) for x in lines) == [
        (date(2026, 10, 4), Decimal("12.34")),  # on both invoices: counted once
        (date(2026, 10, 5), Decimal("10")),
    ]
    assert {x.resource_id for x in lines} == {"prod"} and lines[0].sub_account_name == "Prod"
    assert not any(r.url.path.endswith("/invoices/old") for r in fake.requests)


def test_connect_and_import(client: TestClient, db: Database) -> None:
    fake = FakeVendors()
    client.app.state.http = http(fake)  # type: ignore[attr-defined]
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    c = sign_in_client(client, db, owner, org_id)
    missing = c.post(
        "/api/v1/connections/saas",
        json={"provider": "datadog", "name": "DD", "settings": {"site": "datadoghq.eu"}},
    )
    assert missing.json()["code"] == "missing_fields" and "api_key" in missing.json()["detail"]
    body = {
        "provider": "datadog",
        "name": "Datadog",
        "settings": {"site": "datadoghq.eu", "unexpected": "dropped"},
        "secret": {"api_key": "ddapi", "app_key": "ddapp"},
    }
    fake.deny = True
    assert c.post("/api/v1/connections/saas", json=body).json()["code"] == "connector_check_failed"
    fake.deny = False
    r = c.post("/api/v1/connections/saas", json=body)
    assert r.status_code == 201, r.text
    conn = r.json()
    assert conn["provider"] == "datadog" and conn["saas"] == {"site": "datadoghq.eu"}
    assert "ddapi" not in r.text and "ddapp" not in c.get("/api/v1/connections").text
    with db.session() as s:
        set_tenant(s, bypass=True)
        stored = s.get(CloudConnection, uuid.UUID(conn["id"]))
        assert stored is not None and json.loads(stored.secret or "{}") == {
            "api_key": "ddapi",
            "app_key": "ddapp",
        }
        runs = saas.import_connection(s, http(fake), stored.id, months=1, today=date(2026, 10, 20))
        assert [r.rows for r in runs] == [2]
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        items = s.scalars(select(CostLineItem)).all()
    assert {i.provider for i in items} == {"datadog"} and sum(
        float(i.billed_cost) for i in items
    ) == 549.5
    assert c.post(f"/api/v1/connections/{conn['id']}/import", json={"months": 1}).json() == {
        "status": "queued",
        "source": "datadog",
    }
    # A revoked key marks the connection as needing attention.
    fake.deny = True
    with db.session() as s:
        set_tenant(s, bypass=True)
        saas.import_connection(
            s, http(fake), uuid.UUID(conn["id"]), months=1, today=date(2026, 10, 20)
        )
        stored = s.get(CloudConnection, uuid.UUID(conn["id"]))
        assert (
            stored is not None and stored.status == "error" and "403" in (stored.last_error or "")
        )
