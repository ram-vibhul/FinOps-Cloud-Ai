"""OpenAI, Anthropic, GitHub and Confluent cost connectors (11.29), against fakes."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest

from ccradar.connectors import saas

OCT = date(2026, 10, 1)


def ts(day: int) -> int:
    return int(datetime(2026, 10, day, tzinfo=UTC).timestamp())


class Fake:
    def __init__(self, pages: list[Any]) -> None:
        self.pages, self.requests = pages, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json=self.pages[len(self.requests) - 1])

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


def test_openai_costs_by_project_and_line_item_across_pages() -> None:
    def bucket(day: int, value: float, item: str) -> dict:
        return {
            "start_time": ts(day),
            "end_time": ts(day + 1),
            "results": [
                {
                    "object": "organization.costs.result",
                    "amount": {"value": value, "currency": "usd"},
                    "line_item": item,
                    "project_id": "proj_1",
                },
                {"amount": {"value": 0, "currency": "usd"}, "line_item": "x", "project_id": None},
            ],
        }

    fake = Fake(
        [
            {"data": [bucket(1, 1.25, "gpt-4o, input")], "has_more": True, "next_page": "p2"},
            {"data": [bucket(2, 3.5, "gpt-4o, output")], "has_more": False, "next_page": None},
        ]
    )
    lines = saas.openai_month(fake.client(), {}, {"admin_key": "admin-test"}, OCT)
    assert [(x.charge_date, x.usage_type, x.billed_cost, x.billing_currency) for x in lines] == [
        (date(2026, 10, 1), "gpt-4o, input", Decimal("1.25"), "USD"),
        (date(2026, 10, 2), "gpt-4o, output", Decimal("3.5"), "USD"),
    ]
    first, second = fake.requests
    assert first.url.host == "api.openai.com" and first.headers["authorization"] == (
        "Bearer admin-test"
    )
    assert first.url.params.get_list("group_by") == ["project_id", "line_item"]
    assert second.url.params["page"] == "p2" and lines[0].sub_account_id == "proj_1"


def test_anthropic_cost_report_in_cents() -> None:
    fake = Fake(
        [
            {
                "data": [
                    {
                        "starting_at": "2026-10-03T00:00:00Z",
                        "ending_at": "2026-10-04T00:00:00Z",
                        "results": [
                            {
                                "amount": "12345.6",
                                "currency": "USD",
                                "description": "Claude Opus Usage - Output Tokens",
                                "model": "claude-opus-5-5",
                                "workspace_id": "wrkspc_1",
                                "cost_type": "tokens",
                                "token_type": "output_tokens",
                            },
                            {"amount": "0", "currency": "USD"},
                        ],
                    }
                ],
                "has_more": False,
                "next_page": None,
            }
        ]
    )
    (line,) = saas.anthropic_month(fake.client(), {}, {"admin_key": "sk-ant-admin-test"}, OCT)
    assert line.billed_cost == Decimal("123.456") and line.charge_date == date(2026, 10, 3)
    assert line.sub_account_id == "wrkspc_1" and line.sku_id == "claude-opus-5-5"
    assert line.extras == {"cost_type": "tokens", "token_type": "output_tokens"}
    req = fake.requests[0]
    assert req.headers["x-api-key"] == "sk-ant-admin-test" and req.url.host == "api.anthropic.com"
    assert req.url.params["starting_at"] == "2026-10-01T00:00:00Z"
    assert req.url.params["ending_at"] == "2026-11-01T00:00:00Z"


def test_github_usage_net_of_discounts() -> None:
    item = {
        "date": "2026-10-07T00:00:00Z",
        "product": "actions",
        "sku": "actions_linux",
        "quantity": 1200,
        "unitType": "minutes",
        "pricePerUnit": 0.008,
        "grossAmount": 9.6,
        "discountAmount": 9.6,
        "netAmount": 0,
        "organizationName": "acme",
        "repositoryName": "acme/api",
    }
    paid = {**item, "date": "2026-10-08T00:00:00Z", "discountAmount": 0, "netAmount": 9.6}
    fake = Fake([{"usageItems": [item, paid, {**item, "date": "2026-09-30T00:00:00Z"}]}])
    lines = saas.github_month(fake.client(), {"org": "acme"}, {"token": "ghp-test"}, OCT)
    assert [(x.charge_date, x.billed_cost, x.list_cost) for x in lines] == [
        (date(2026, 10, 7), Decimal("0"), Decimal("9.6")),  # free minutes still show usage
        (date(2026, 10, 8), Decimal("9.6"), Decimal("9.6")),
    ]
    assert lines[0].service_name == "GitHub Actions" and lines[0].resource_id == "acme/api"
    assert fake.requests[0].url.path == "/organizations/acme/settings/billing/usage"
    with pytest.raises(saas.ConnectorError):
        saas.github_month(fake.client(), {"org": "../evil"}, {"token": "t"}, OCT)


def test_confluent_costs_paged_and_only_finished_days(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(saas, "utcnow", lambda: datetime(2026, 10, 20, 12, tzinfo=UTC))
    row = {
        "start_date": "2026-10-02",
        "end_date": "2026-10-03",
        "granularity": "DAILY",
        "line_type": "KAFKA_NUM_CKUS",
        "product": "KAFKA",
        "resource": {"id": "lkc-1", "display_name": "orders", "environment": {"id": "env-1"}},
        "amount": 40.5,
        "original_amount": 45,
        "quantity": 24,
        "unit": "CKU-hour",
    }
    fake = Fake(
        [
            {"data": [row], "metadata": {"next": "https://api.confluent.cloud/x?page_token=t2"}},
            {
                "data": [{**row, "start_date": "2026-10-03", "amount": 1, "original_amount": 1}],
                "metadata": {},
            },
        ]
    )
    lines = saas.confluent_month(fake.client(), {}, {"api_key": "k", "api_secret": "s"}, OCT)
    assert [(x.charge_date, x.billed_cost, x.list_cost) for x in lines] == [
        (date(2026, 10, 2), Decimal("40.5"), Decimal("45")),
        (date(2026, 10, 3), Decimal("1"), Decimal("1")),
    ]
    assert lines[0].service_name == "Confluent Kafka" and lines[0].sub_account_id == "env-1"
    first, second = fake.requests
    assert first.url.params["end_date"] == "2026-10-20" and second.url.params["page_token"] == "t2"
    assert first.headers["authorization"].startswith("Basic ")
    assert saas.confluent_month(fake.client(), {}, {"api_key": "k", "api_secret": "s"},
                                date(2026, 11, 1)) == []  # fmt: skip
