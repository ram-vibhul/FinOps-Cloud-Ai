"""Azure and Google Cloud commitments in the inventory (11.22), against fakes."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from ccradar.clouds import other_commitments
from ccradar.clouds.aws.commitment_inventory import expiry_alerts
from ccradar.db import Database
from ccradar.db.tenant import set_tenant
from ccradar.models import Commitment
from tests.helpers import make_org, sign_in_client
from tests.test_other_clouds_waste import Response, connection

RES = "/providers/microsoft.capacity/reservationOrders/o1/reservations/r1"
PLAN = "/providers/Microsoft.BillingBenefits/savingsPlanOrders/o2/savingsPlans/p1"
CUD = "https://compute/projects/shop-prod/regions/asia-south1/commitments/cud-1"


class FakeAzure:
    def __init__(self, plans: bool = True, reservations: bool = True) -> None:
        self.plans, self.reservations = plans, reservations

    def request(self, method: str, url: str, **_: Any) -> Response:
        assert method == "GET"
        if "Microsoft.Capacity/reservations" in url:
            if not self.reservations:
                raise PermissionError("403")
            return Response({"value": [{
                "id": RES, "location": "centralindia", "sku": {"name": "Standard_D4s_v5"},
                "properties": {
                    "reservedResourceType": "VirtualMachines", "quantity": 3, "term": "P3Y",
                    "billingPlan": "P1M", "displayProvisioningState": "Succeeded",
                    "effectiveDateTime": "2024-01-10T00:00:00Z",
                    "expiryDateTimeInUtc": "2027-01-10T00:00:00Z",
                    "utilization": {"aggregates": [
                        {"grain": 1.0, "value": 50.0, "valueUnit": "percentage"},
                        {"grain": 30.0, "value": 72.5, "valueUnit": "percentage"},
                    ]},
                },
            }, {
                "id": RES.replace("r1", "old"), "location": "centralindia",
                "properties": {"displayProvisioningState": "Expired", "term": "P1Y"},
            }]})  # fmt: skip
        if "Microsoft.BillingBenefits/savingsPlans" in url:
            if not self.plans:
                raise PermissionError("403")
            return Response({"value": [{
                "id": PLAN, "sku": {"name": "Compute_Savings_Plan"},
                "properties": {
                    "displayName": "Compute plan", "term": "P1Y", "billingPlan": "Upfront",
                    "commitment": {"grain": "Hourly", "currencyCode": "USD", "amount": 2.5},
                    "provisioningState": "Succeeded", "effectiveDateTime": "2026-02-01T00:00:00Z",
                    "expiryDateTime": "2027-02-01T00:00:00Z",
                    "utilization": {"aggregates": [{"grain": 7, "value": 98.0}]},
                },
            }]})  # fmt: skip
        raise AssertionError(url)


class FakeGcp:
    def request(self, method: str, url: str, **_: Any) -> dict:
        assert url.endswith("/shop-prod/aggregated/commitments"), url
        return {"items": {
            "regions/asia-south1": {"commitments": [{
                "selfLink": CUD, "name": "cud-1", "region": "https://x/regions/asia-south1",
                "status": "ACTIVE", "plan": "THIRTY_SIX_MONTH", "type": "GENERAL_PURPOSE_N2",
                "category": "MACHINE", "startTimestamp": "2025-01-01T00:00:00.000-08:00",
                "endTimestamp": "2028-01-01T00:00:00.000-08:00",
                "resources": [
                    {"type": "VCPU", "amount": "8"}, {"type": "MEMORY", "amount": "32768"},
                ],
            }]},
            "regions/us-east1": {"warning": {"code": "NO_RESULTS_ON_PAGE"}},
        }}  # fmt: skip


def _rows(db: Database, org_id) -> dict[str, Commitment]:
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        return {
            c.kind + ":" + c.commitment_id.rsplit("/", 1)[-1]: c
            for c in s.scalars(select(Commitment))
        }


def test_azure_reservations_and_savings_plans(db: Database) -> None:
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    conn = connection(db, org_id, "azure")
    with db.session() as s:
        out = other_commitments.collect(s, FakeAzure(), conn)
    assert out == {"count": 3, "errors": {}}
    rows = _rows(db, org_id)
    res, plan = rows["azure_reservation:r1"], rows["azure_savings_plan:p1"]
    assert res.description == "VirtualMachines Standard_D4s_v5" and res.count == 3
    assert res.term_years == 3 and res.payment_option == "Monthly" and res.state == "active"
    assert res.utilization_pct == 72.5  # the 30-day figure wins
    assert res.ends_at == datetime(2027, 1, 10, tzinfo=UTC) and res.account_id is None
    assert rows["azure_reservation:old"].state == "retired"
    assert plan.hourly_commitment == 2.5 and plan.payment_option == "All Upfront"
    assert plan.utilization_pct == 98.0 and plan.term_years == 1

    with db.session() as s:  # savings plans not readable now: they are kept, not retired
        out = other_commitments.collect(s, FakeAzure(plans=False), conn)
    assert out["errors"] == {"azure_savings_plans": "PermissionError"}
    assert _rows(db, org_id)["azure_savings_plan:p1"].state == "active"


def test_gcp_committed_use_discounts(db: Database, monkeypatch) -> None:
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    conn = connection(db, org_id, "gcp")
    import ccradar.clouds.gcp.recommender as rec

    monkeypatch.setattr(rec, "locations", lambda db, org: [("shop-prod", "asia-south1", ())])
    with db.session() as s:
        assert other_commitments.collect(s, FakeGcp(), conn)["count"] == 1
    (cud,) = _rows(db, org_id).values()
    assert cud.kind == "gcp_cud" and cud.account_id == "shop-prod"
    assert cud.description == "General Purpose N2 8 vCPU, 32 GB memory"
    assert cud.region == "asia-south1" and cud.term_years == 3 and cud.state == "active"
    assert cud.utilization_pct is None and cud.ends_at and cud.ends_at.year == 2028

    with db.session() as s:  # expiry alerts are shared with AWS
        set_tenant(s, org_id=org_id)
        expiry_alerts(s, today=date(2027, 12, 20))
        assert s.scalar(select(Commitment.alerts_sent)) == [90, 30]


def test_inventory_api_lists_other_clouds(client: TestClient, db: Database, monkeypatch) -> None:
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    conn = connection(db, org_id, "azure")
    import ccradar.clouds.azure.client as az

    monkeypatch.setattr(az, "for_connection", lambda c, http=None: FakeAzure(plans=False))
    o = sign_in_client(client, db, owner, org_id)
    r = o.post("/api/v1/commitments/refresh")
    assert r.status_code == 200, r.text
    assert r.json()["commitments"] == 2
    body = o.get("/api/v1/commitments/inventory").json()
    assert body["kinds"]["azure_reservation"] == "Azure reservation"
    assert body["kinds"]["gcp_cud"] == "Google committed use discount"
    assert [i["kind"] for i in body["items"]] == ["azure_reservation"]  # expired one hidden
    assert body["items"][0]["utilization_pct"] == 72.5
    assert conn
