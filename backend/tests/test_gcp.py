from __future__ import annotations

import json
import uuid
from datetime import date, timedelta
from typing import Any
from urllib.parse import parse_qs

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import select

from ccradar.clouds.gcp import costs as gcp_costs
from ccradar.clouds.gcp import recommender
from ccradar.clouds.gcp.client import GcpClient, GcpError, parse_key
from ccradar.db import Database
from ccradar.db.base import utcnow
from ccradar.db.tenant import set_tenant
from ccradar.models import CloudConnection, CostLineItem, ExternalRecommendation
from tests.helpers import make_org, sign_in_client

_private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = _private.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
).decode()
KEY = {
    "type": "service_account",
    "project_id": "acme-billing",
    "private_key_id": "k1",
    "private_key": PEM,
    "client_email": "ccradar@acme-billing.iam.gserviceaccount.com",
}
VM1 = "projects/web-prod/zones/europe-west1-b/instances/vm-1"
REC1 = "projects/web-prod/locations/europe-west1-b/recommenders/x/recommendations/r1"
FIELDS = [
    "day", "project_id", "project_name", "service", "sku", "sku_id", "region", "zone", "resource",
    "cost_type", "currency", "labels", "cost", "credits", "quantity", "unit",
]  # fmt: skip


def row(*values: Any) -> dict[str, Any]:
    return {"f": [{"v": v} for v in values]}


class FakeGoogle:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.denied = False  # Recommender permission
        self.bad_login = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = request.url
        if url.host == "oauth2.googleapis.com":
            form = parse_qs(request.content.decode())
            claims = jwt.decode(
                form["assertion"][0],
                _private.public_key(),
                algorithms=["RS256"],
                audience="https://oauth2.googleapis.com/token",
            )
            assert claims["iss"] == KEY["client_email"]
            if self.bad_login:
                return httpx.Response(
                    400, json={"error": "invalid_grant", "error_description": "Invalid JWT"}
                )
            return httpx.Response(200, json={"access_token": "gtok", "expires_in": 3600})
        assert request.headers["authorization"] == "Bearer gtok"
        if url.host == "bigquery.googleapis.com":
            schema = {"fields": [{"name": n} for n in FIELDS]}
            if request.method == "POST":
                body = json.loads(request.content)
                assert "`acme-billing.billing.gcp_billing_export_resource_v1_X`" in body["query"]
                assert {p["name"] for p in body["queryParameters"]} == {"month", "start", "end"}
                assert body["parameterMode"] == "NAMED"
                return httpx.Response(
                    200,
                    json={"jobComplete": False, "jobReference": {"jobId": "j1", "location": "EU"}},
                )
            if url.params.get("pageToken") != "p2":
                return httpx.Response(
                    200,
                    json={
                        "jobComplete": True,
                        "schema": schema,
                        "pageToken": "p2",
                        "rows": [
                            row(
                                "2026-10-01",
                                "web-prod",
                                "Web",
                                "Compute Engine",
                                "N2 Instance Core running in Belgium",
                                "sku1",
                                "europe-west1",
                                "europe-west1-b",
                                "projects/web-prod/zones/europe-west1-b/instances/vm-1",
                                "regular",
                                "EUR",
                                '[{"key":"team","value":"web"}]',
                                "20.0",
                                "-5.0",
                                "24",
                                "hour",
                            ),
                        ],
                    },
                )
            return httpx.Response(
                200,
                json={
                    "jobComplete": True,
                    "schema": schema,
                    "rows": [
                        row(
                            "2026-10-02",
                            "web-prod",
                            "Web",
                            "Compute Engine",
                            "Commitment v1: Cpu in Belgium for 1 Year",
                            "sku2",
                            "europe-west1",
                            None,
                            None,
                            "regular",
                            "EUR",
                            "[]",
                            "30.0",
                            "0",
                            None,
                            None,
                        ),
                        row(
                            "2026-10-02",
                            "web-prod",
                            "Web",
                            "Invoice",
                            "Tax",
                            "sku3",
                            None,
                            None,
                            None,
                            "tax",
                            "EUR",
                            "[]",
                            "4.0",
                            "0",
                            None,
                            None,
                        ),
                        row(
                            "2026-10-03",
                            "web-prod",
                            "Web",
                            "Support",
                            "Promo",
                            "sku4",
                            None,
                            None,
                            None,
                            "regular",
                            "EUR",
                            "[]",
                            "0",
                            "0",
                            None,
                            None,
                        ),
                    ],
                },
            )
        if url.host == "recommender.googleapis.com":
            if self.denied:
                return httpx.Response(
                    403, json={"error": {"status": "PERMISSION_DENIED", "message": "no"}}
                )
            if "MachineTypeRecommender" not in url.path:
                return httpx.Response(200, json={})
            assert url.params["filter"] == "stateInfo.state = ACTIVE"
            return httpx.Response(
                200,
                json={
                    "recommendations": [
                        {
                            "name": REC1,
                            "description": "Change n2-standard-8 to n2-standard-4.",
                            "recommenderSubtype": "CHANGE_MACHINE_TYPE",
                            "primaryImpact": {
                                "category": "COST",
                                "costProjection": {
                                    "cost": {
                                        "currencyCode": "EUR",
                                        "units": "-60",
                                        "nanos": -500000000,
                                    },
                                    "duration": "2592000s",
                                },
                            },
                            "content": {
                                "overview": {"resourceName": f"//compute.googleapis.com/{VM1}"}
                            },
                        },
                        {"name": "r2", "primaryImpact": {"category": "PERFORMANCE"}},
                    ]
                },
            )
        return httpx.Response(404, json={"error": {"status": "NOT_FOUND", "message": str(url)}})


def gcp(fake: FakeGoogle) -> GcpClient:
    return GcpClient(httpx.Client(transport=httpx.MockTransport(fake)), KEY)


CFG = {
    "table": "acme-billing.billing.gcp_billing_export_resource_v1_X",
    "query_project": "acme-billing",
    "detailed": True,
}


def test_key_checks() -> None:
    assert parse_key(json.dumps(KEY))["project_id"] == "acme-billing"
    for bad in ("not json", json.dumps({"type": "user"}), json.dumps({**KEY, "private_key": ""})):
        with pytest.raises(GcpError):
            parse_key(bad)


def test_billing_export_query_waits_pages_and_maps_rows() -> None:
    fake = FakeGoogle()
    lines = gcp_costs.fetch_month(gcp(fake), CFG, date(2026, 10, 1))
    assert len(lines) == 3  # the zero row is skipped
    vm, cud, tax = lines
    assert vm.provider == "gcp" and vm.sub_account_id == "web-prod" and vm.sub_account_name == "Web"
    assert vm.billed_cost == 15 and vm.effective_cost == 15  # credits included
    assert vm.region == "europe-west1" and vm.availability_zone == "europe-west1-b"
    assert (vm.resource_id or "").endswith("/instances/vm-1") and vm.tags == {"team": "web"}
    assert (
        vm.consumed_quantity == 24 and vm.consumed_unit == "hour" and vm.billing_currency == "EUR"
    )
    assert cud.charge_category == "Purchase" and cud.pricing_category == "Committed"
    assert tax.charge_category == "Tax"
    month = next(
        p for p in json.loads(fake.requests[1].content)["queryParameters"] if p["name"] == "month"
    )
    assert month["parameterValue"]["value"] == "202610"
    gets = [r for r in fake.requests if r.method == "GET"]
    assert gets[0].url.params["location"] == "EU" and gets[1].url.params["pageToken"] == "p2"
    with pytest.raises(GcpError, match=r"project\.dataset\.table"):
        gcp_costs.build_sql("x; DROP TABLE y", False)
    assert "CAST(NULL AS STRING) AS resource" in gcp_costs.build_sql("p-abcd.ds.t", False)


def test_connect_import_and_recommender(client: TestClient, db: Database) -> None:
    fake = FakeGoogle()
    client.app.state.http = httpx.Client(transport=httpx.MockTransport(fake))  # type: ignore[attr-defined]
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    c = sign_in_client(client, db, owner, org_id)
    base = {
        "name": "GCP",
        "service_account_key": json.dumps(KEY),
        "table": CFG["table"],
        "detailed": True,
    }
    bad = c.post("/api/v1/connections/gcp", json={**base, "table": "nope"})
    assert bad.json()["code"] == "bad_table"
    fake.bad_login = True
    assert c.post("/api/v1/connections/gcp", json=base).json()["code"] == "gcp_sign_in_failed"
    fake.bad_login = False
    r = c.post("/api/v1/connections/gcp", json=base)
    assert r.status_code == 201, r.text
    conn = r.json()
    assert conn["provider"] == "gcp" and conn["account_id"] == "acme-billing"
    assert (
        conn["gcp"]["client_email"] == KEY["client_email"]
        and conn["gcp"]["query_project"] == "acme-billing"
    )
    assert "PRIVATE KEY" not in r.text and "PRIVATE KEY" not in c.get("/api/v1/connections").text
    with db.session() as s:
        set_tenant(s, bypass=True)
        stored = s.get(CloudConnection, uuid.UUID(conn["id"]))
        assert stored is not None
        runs = gcp_costs.import_connection(
            s, gcp(fake), stored.id, months=1, today=date(2026, 10, 20)
        )
        assert [r.rows for r in runs] == [3]
    # Recommender asks only where the bill shows spend; move the imported days into the last 30.
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        for li in s.scalars(select(CostLineItem)):
            li.charge_date = utcnow().date() - timedelta(days=2)
        s.flush()
        set_tenant(s, bypass=True)
        out = recommender.collect(s, gcp(fake), uuid.UUID(conn["id"]))
    assert out == {"count": 1, "errors": {}}
    rec_calls = [r.url.path for r in fake.requests if r.url.host == "recommender.googleapis.com"]
    assert any(
        "/locations/europe-west1-b/recommenders/google.compute.instance.MachineTypeRecommender" in p
        for p in rec_calls
    )
    assert any(
        "/locations/europe-west1/recommenders/google.compute.address.IdleResourceRecommender" in p
        for p in rec_calls
    )
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        rec = s.scalars(select(ExternalRecommendation)).one()
    assert rec.action == "rightsize" and rec.savings_monthly == 60.5 and rec.currency == "EUR"
    assert rec.account_id == "web-prod" and (rec.resource_id or "").endswith("/instances/vm-1")
    fake.denied = True
    with db.session() as s:
        set_tenant(s, bypass=True)
        errs = recommender.collect(s, gcp(fake), uuid.UUID(conn["id"]))["errors"]
    assert errs and all("PERMISSION_DENIED" in v for v in errs.values())
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        assert len(s.scalars(select(ExternalRecommendation)).all()) == 1  # kept after a failed run
    assert c.post(f"/api/v1/connections/{conn['id']}/import", json={"months": 1}).json() == {
        "status": "queued",
        "source": "gcp",
    }


def test_monthly_saving_from_projection() -> None:
    assert recommender._monthly(
        {"costProjection": {"cost": {"units": "-14", "nanos": 0}, "duration": "604800s"}}
    ) == (60.0, "USD")
