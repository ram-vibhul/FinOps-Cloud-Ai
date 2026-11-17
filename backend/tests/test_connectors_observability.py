"""Fastly, Elastic Cloud and Grafana Cloud cost connectors (11.20), against fakes."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest

from ccradar.connectors import saas

OCT = date(2026, 10, 1)


class Fake:
    def __init__(self, answers: list[Any]) -> None:
        self.answers, self.requests = answers, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json=self.answers[len(self.requests) - 1])

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


def test_fastly_invoices_across_pages_with_a_credit() -> None:
    fake = Fake([
        {"data": [{"invoice_id": "11", "customer_id": "c1", "currency_code": "USD",
                   "billing_start_date": "2026-10-01T00:00:00Z",
                   "transaction_line_items": [
                       {"amount": 120.5, "product_name": "CDN", "region": "North America",
                        "usage_type": "Bandwidth", "units": 4000, "product_group": "Delivery"},
                       {"amount": -20, "product_name": "CDN", "credit_coupon_code": "PROMO"},
                       {"amount": 0, "product_name": "Image Optimizer"},
                   ]}],
         "meta": {"next_cursor": "c2"}},
        {"data": [{"invoice_id": "12", "billing_start_date": "2026-09-01T00:00:00Z",
                   "transaction_line_items": [{"amount": 5, "product_name": "Old"}]}],
         "meta": {}},
    ])  # fmt: skip
    lines = saas.fastly_month(fake.client(), {}, {"token": "f"}, OCT)
    assert [(x.service_name, x.charge_category, x.billed_cost) for x in lines] == [
        ("Fastly CDN", "Usage", Decimal("120.5")),
        ("Fastly CDN", "Credit", Decimal("-20")),
    ]
    assert lines[0].region == "North America" and lines[0].consumed_quantity == 4000
    first, second = fake.requests
    assert first.headers["fastly-key"] == "f" and first.url.params["billing_start_date"] == (
        "2026-10-01"
    )
    assert second.url.params["cursor"] == "c2"


def test_fastly_current_month_is_month_to_date(monkeypatch) -> None:
    monkeypatch.setattr(saas, "utcnow", lambda: datetime(2026, 10, 9))
    fake = Fake([{"transaction_line_items": [{"amount": 7, "product_name": "Compute"}]}])
    (line,) = saas.fastly_month(fake.client(), {}, {"token": "f"}, OCT)
    assert line.billed_cost == 7 and fake.requests[0].url.path.endswith("/month-to-date")


def test_elastic_ecu_converted_at_the_customer_rate() -> None:
    fake = Fake([{
        "total_ecu": 130,
        "products": [{"type": "elasticsearch", "total_ecu": 130, "product_line_items": [
            {"name": "gcp.es.datahot.n2.68x10x45", "sku": "sku-1", "unit": "hour",
             "total_ecu": 100, "quantity": {"value": 744, "formatted_value": "744 hours"}},
            {"name": "Data transfer out", "sku": "sku-2", "unit": "GB", "total_ecu": 30,
             "quantity": {"value": 120}},
            {"name": "Free", "total_ecu": 0},
        ]}],
    }])  # fmt: skip
    cfg = {"organization_id": "123456789", "usd_per_ecu": "0.9"}
    lines = saas.elastic_month(fake.client(), cfg, {"api_key": "ek"}, OCT)
    assert [(x.service_name, x.billed_cost) for x in lines] == [
        ("Elastic Elasticsearch", Decimal("90.0000")),
        ("Elastic Elasticsearch", Decimal("27.0000")),
    ]
    assert lines[0].extras == {"ecu": "100", "usd_per_ecu": "0.9"}
    assert lines[0].consumed_quantity == 744 and lines[0].consumed_unit == "hour"
    (req,) = fake.requests
    assert req.url.host == "api.elastic-cloud.com" and req.headers["authorization"] == "ApiKey ek"
    assert req.url.path == "/api/v2/billing/organizations/123456789/costs/items"
    for bad in ({"organization_id": "../x"}, {"organization_id": "1", "usd_per_ecu": "x"}):
        with pytest.raises(saas.ConnectorError):
            saas.elastic_month(fake.client(), bad, {"api_key": "ek"}, OCT)


def test_grafana_split_by_stack_with_the_rest_unattributed() -> None:
    fake = Fake([
        {"items": [
            {"dimensionId": "hm", "dimensionName": "Metrics", "amountDue": 100.0,
             "unit": "series", "totalUsage": 50000, "description": "Active series",
             "usages": [
                 {"stackId": 501, "stackName": "prod", "attributedCost": 70.0,
                  "totalUsage": 35000, "stackLabels": {"team": "platform"}},
                 {"stackId": 502, "stackName": "dev", "attributedCost": 20.0},
             ]},
         ], "page": 1, "pages": 2},
        {"items": [
            {"dimensionId": "hl", "dimensionName": "Logs", "amountDue": 12.5,
             "totalUsage": 300, "unit": "GB", "usages": []},
            {"dimensionId": "irm", "dimensionName": "IRM", "amountDue": 0},
        ], "page": 2, "pages": 2},
    ])  # fmt: skip
    lines = saas.grafana_month(fake.client(), {"org": "acme"}, {"token": "g"}, OCT)
    assert [(x.service_name, x.resource_name, x.billed_cost) for x in lines] == [
        ("Grafana Metrics", "prod", Decimal("70.0")),
        ("Grafana Metrics", "dev", Decimal("20.0")),
        ("Grafana Metrics", None, Decimal("10.0")),
        ("Grafana Logs", None, Decimal("12.5")),
    ]
    assert lines[0].tags == {"team": "platform"} and lines[0].resource_id == "501"
    assert lines[3].consumed_quantity == 300
    first, second = fake.requests
    assert first.url.path == "/api/orgs/acme/billed-usage"
    assert (first.url.params["month"], first.url.params["year"]) == ("10", "2026")
    assert second.url.params["page"] == "2" and first.headers["authorization"] == "Bearer g"
    with pytest.raises(saas.ConnectorError):
        saas.grafana_month(fake.client(), {"org": "a/b"}, {"token": "g"}, OCT)
