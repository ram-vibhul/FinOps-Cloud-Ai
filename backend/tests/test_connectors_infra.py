"""Vercel, Cloudflare, DigitalOcean and Linode cost connectors (11.20), against fakes."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any

import httpx
import pytest

from ccradar.connectors import saas

OCT = date(2026, 10, 1)
INV = "22737513-0ea7-4206-8ceb-98a575af7681"


class Fake:
    """Answers each request with the next response (a dict as JSON, or text)."""

    def __init__(self, answers: list[Any]) -> None:
        self.answers, self.requests = answers, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = self.answers[len(self.requests) - 1]
        if isinstance(body, str):
            return httpx.Response(200, text=body)
        return httpx.Response(200, json=body)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


def test_vercel_focus_rows_with_projects() -> None:
    rows = [
        {
            "BilledCost": 12.5, "EffectiveCost": 10.0, "BillingCurrency": "USD",
            "ChargeCategory": "Usage", "PricingCategory": "Standard",
            "ChargePeriodStart": "2026-10-03T00:00:00Z", "ChargePeriodEnd": "2026-10-04T00:00:00Z",
            "ConsumedQuantity": 250, "ConsumedUnit": "GB", "ServiceName": "Fast Data Transfer",
            "ServiceCategory": "Networking", "SkuId": "fdt", "SubAccountId": "team_1",
            "SubAccountName": "Acme", "RegionId": "iad1",
            "Tags": {"ProjectId": "prj_1", "ProjectName": "shop"},
        },
        {
            "BilledCost": 0, "EffectiveCost": 0, "ChargeCategory": "Usage",
            "ChargePeriodStart": "2026-10-03T00:00:00Z", "ServiceName": "Edge Requests",
        },
        {
            "BilledCost": 20, "ChargeCategory": "Purchase", "PricingCategory": "Committed",
            "ChargePeriodStart": "2026-10-01T00:00:00Z", "ServiceName": "Pro seats", "Tags": {},
        },
    ]  # fmt: skip
    fake = Fake(["\n".join(json.dumps(r) for r in rows) + "\n"])
    lines = saas.vercel_month(fake.client(), {"team_id": "team_1"}, {"token": "t"}, OCT)
    first, seat = lines
    assert (first.billed_cost, first.effective_cost) == (Decimal("12.5"), Decimal("10.0"))
    assert first.resource_id == "prj_1" and first.resource_name == "shop"
    assert first.region == "iad1" and first.consumed_unit == "GB"
    assert first.service_category == "Networking" and first.sub_account_id == "team_1"
    assert seat.charge_category == "Purchase" and seat.pricing_category == "Committed"
    assert seat.effective_cost == 20  # no EffectiveCost: the billed cost
    (req,) = fake.requests
    assert req.url.host == "api.vercel.com" and req.headers["authorization"] == "Bearer t"
    assert req.url.params["teamId"] == "team_1"
    assert req.url.params["from"] == "2026-10-01T00:00:00Z"
    assert req.url.params["to"] == "2026-11-01T00:00:00Z"
    with pytest.raises(saas.ConnectorError):
        saas.vercel_month(fake.client(), {"team_id": "../x"}, {"token": "t"}, OCT)


def test_cloudflare_billable_usage() -> None:
    fake = Fake([{
        "success": True, "errors": [], "messages": [],
        "result": [
            {
                "ChargeCategory": "Usage", "ChargeDescription": "Workers requests",
                "ChargePeriodStart": "2026-10-05T00:00:00Z",
                "ChargePeriodEnd": "2026-10-06T00:00:00Z",
                "ConsumedUnit": "requests", "ConsumedQuantity": 1000000, "BilledCost": 0.3,
                "ContractedCost": 0.3, "BillingCurrency": "USD",
                "x_BillableMetricId": "workers_requests", "x_ProductFamilyName": "Workers",
                "x_ZoneId": None,
            },
            {"ChargePeriodStart": "2026-09-30T00:00:00Z", "BilledCost": 9,
             "x_ProductFamilyName": "R2"},
        ],
    }])  # fmt: skip
    account = "0123456789abcdef0123456789abcdef"
    (line,) = saas.cloudflare_month(fake.client(), {"account_id": account}, {"api_token": "c"}, OCT)
    assert line.provider == "cloudflare" and line.service_name == "Workers"
    assert line.sku_id == "workers_requests" and line.usage_type == "Workers requests"
    assert line.billed_cost == Decimal("0.3") and line.charge_date == date(2026, 10, 5)
    (req,) = fake.requests
    assert req.url.path == f"/client/v4/accounts/{account}/billable/usage"
    assert (req.url.params["from"], req.url.params["to"]) == ("2026-10-01", "2026-10-31")
    with pytest.raises(saas.ConnectorError):
        saas.cloudflare_month(fake.client(), {"account_id": "nope"}, {"api_token": "c"}, OCT)
    refused = Fake([{"success": False, "errors": [{"code": 10000, "message": "auth"}]}])
    with pytest.raises(saas.ConnectorError, match="refused"):
        saas.cloudflare_month(refused.client(), {"account_id": account}, {"api_token": "c"}, OCT)


def test_digitalocean_closed_invoice_found_on_a_later_page() -> None:
    nxt = "https://api.digitalocean.com/v2/customers/my/invoices?page=2&per_page=200"
    items_next = f"https://api.digitalocean.com/v2/customers/my/invoices/{INV}?page=2&per_page=200"
    fake = Fake([
        {"invoices": [{"invoice_uuid": "x", "invoice_period": "2026-11"}],
         "invoice_preview": {"invoice_uuid": "p", "invoice_period": "2026-12"},
         "links": {"pages": {"next": nxt}}},
        {"invoices": [{"invoice_uuid": "x", "invoice_period": "2026-11"}],
         "links": {"pages": {"next": nxt}}},
        {"invoices": [{"invoice_uuid": INV, "invoice_period": "2026-10"}], "links": {}},
        {"invoice_items": [
            {"product": "Droplets", "resource_uuid": "d-1", "group_description": "web-1",
             "description": "s-2vcpu-4gb", "amount": "24.00", "duration": "744",
             "duration_unit": "Hours", "start_time": "2026-10-01T00:00:00Z",
             "end_time": "2026-11-01T00:00:00Z"},
         ], "links": {"pages": {"next": items_next}}},
        {"invoice_items": [
            {"product": "Spaces Subscription", "description": "Spaces", "amount": "5.00",
             "start_time": "2026-09-20T00:00:00Z"},
            {"product": "Droplets", "amount": "0.00"},
        ], "links": {"pages": {}}},
    ])  # fmt: skip
    lines = saas.digitalocean_month(fake.client(), {}, {"token": "do"}, OCT)
    assert [(x.service_name, x.billed_cost, x.charge_date) for x in lines] == [
        ("DigitalOcean Droplets", Decimal("24.00"), date(2026, 10, 1)),
        ("DigitalOcean Spaces Subscription", Decimal("5.00"), date(2026, 10, 1)),  # clamped
    ]
    assert lines[0].resource_id == "d-1" and lines[0].resource_name == "web-1"
    assert lines[0].consumed_quantity == 744 and lines[0].consumed_unit == "Hours"
    assert fake.requests[3].url.path.endswith(INV)


def test_digitalocean_current_month_uses_the_preview() -> None:
    fake = Fake([
        {"invoices": [], "invoice_preview": {"invoice_uuid": INV, "invoice_period": "2026-10"}},
        {"invoice_items": [{"product": "Droplets", "amount": "3.10",
                            "start_time": "2026-10-01T00:00:00Z"}]},
    ])  # fmt: skip
    (line,) = saas.digitalocean_month(fake.client(), {}, {"token": "do"}, OCT)
    assert line.billed_cost == Decimal("3.10")
    nothing = Fake([{"invoices": []}, {"invoices": []}])
    assert saas.digitalocean_month(nothing.client(), {}, {"token": "do"}, OCT) == []


def test_linode_invoice_items_with_tax() -> None:
    fake = Fake([
        {"data": [{"id": 387, "date": "2026-11-01T00:01:01", "total": 30.0}],
         "page": 1, "pages": 1},
        {"data": [
            {"amount": 20.2, "from": "2026-10-01T00:00:00", "to": "2026-10-31T23:59:59",
             "label": "Linode 4GB - web1", "quantity": 744, "region": "us-east", "tax": 1.25,
             "type": "hourly"},
            {"amount": 5, "from": "2026-10-01T00:00:00", "label": "NodeBalancer - nb-1",
             "region": "us-east", "tax": 0, "type": "hourly"},
            {"amount": 9, "from": "2026-11-01T00:00:00", "label": "Backup Service - web1",
             "tax": 0, "type": "misc"},
        ], "page": 1, "pages": 1},
    ])  # fmt: skip
    lines = saas.linode_month(fake.client(), {}, {"token": "li"}, OCT)
    assert [(x.service_name, x.resource_name, x.charge_category, x.billed_cost) for x in lines] == [
        ("Akamai Linodes", "Linode 4GB - web1", "Usage", Decimal("20.2")),
        ("Akamai Linodes", "Linode 4GB - web1", "Tax", Decimal("1.25")),
        ("Akamai NodeBalancer", "nb-1", "Usage", Decimal("5")),
    ]
    assert lines[0].region == "us-east" and lines[0].billing_account_id == "387"
    first, items = fake.requests
    assert json.loads(first.headers["x-filter"])["+and"][1] == {
        "date": {"+lt": "2026-12-01T00:00:00"}
    }
    assert items.url.path == "/v4/account/invoices/387/items"
    assert "x-filter" not in items.headers


def test_lines_validate_and_import(db) -> None:
    """Every connector's lines pass the importer's checks."""
    from ccradar.costs.ingest import replace_billing_period
    from tests.helpers import make_org

    fake = Fake([
        "\n".join([json.dumps({"BilledCost": 1, "ChargeCategory": "Weird",
                               "ChargePeriodStart": "2026-10-02T00:00:00Z",
                               "ServiceName": "Builds"})]),
    ])  # fmt: skip
    lines = saas.vercel_month(fake.client(), {"team_id": "acme"}, {"token": "t"}, OCT)
    assert lines[0].charge_category == "Usage" and fake.requests[0].url.params["slug"] == "acme"
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    with db.session() as s:
        run = replace_billing_period(
            s, org_id=org_id, source_kind="vercel", source_key="v", billing_period=OCT, lines=lines
        )
        assert (run.status, run.rows) == ("succeeded", 1)
