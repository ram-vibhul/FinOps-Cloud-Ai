"""New Relic connector (11.29): usage from NerdGraph priced with the customer's rates."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest

from ccradar.connectors import saas

OCT = date(2026, 10, 1)


def day(n: int) -> int:
    return int(datetime(2026, 10, n, tzinfo=UTC).timestamp())


def answer(ingest: list, compute: list, users: list, errors: Any = None) -> dict:
    body: dict[str, Any] = {
        "data": {
            "actor": {
                "account": {
                    "ingest": {"results": ingest},
                    "compute": {"results": compute},
                    "users": {"results": users},
                }
            }
        }
    }
    if errors:
        body["errors"] = errors
    return body


class Fake:
    def __init__(self, body: dict) -> None:
        self.body, self.requests = body, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json=self.body)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


def gb(n: int, source: str, value: float) -> dict:
    return {
        "beginTimeSeconds": day(n),
        "endTimeSeconds": day(n + 1),
        "facet": source,
        "usageMetric": source,
        "sum.GigabytesIngested": value,
    }


def test_usage_priced_with_free_allowance_and_seats() -> None:
    fake = Fake(
        answer(
            ingest=[gb(1, "Logs", 80), gb(1, "Metrics", 30), gb(2, "Logs", 50)],
            compute=[
                {"beginTimeSeconds": day(3), "facet": "CoreCCU", "sum.consumption": 1000},
            ],
            users=[{"latest.FullPlatformUsersBillable": 4, "latest.CoreUsersBillable": 2}],
        )
    )
    cfg = {"account_id": "1234567", "region": "EU", "price_per_gb": "0.5", "price_core_ccu": "0.1"}
    lines = saas.newrelic_month(fake.client(), cfg, {"user_key": "NRAK-test"}, OCT)
    got = [(x.charge_date.day, x.service_name, x.usage_type, x.billed_cost) for x in lines]
    assert got == [
        # 100 GB free: Logs on the 1st (80) is free, Metrics uses the last 20 free GB
        (1, "New Relic Data Ingest", "Metrics", Decimal("5.0000")),
        (2, "New Relic Data Ingest", "Logs", Decimal("25.0000")),
        (3, "New Relic Compute", "CoreCCU", Decimal("100.0000")),
        (1, "New Relic Users", "Full platform users", Decimal("1047.0000")),  # 3 paid seats at 349
        (1, "New Relic Users", "Core users", Decimal("98.0000")),
    ]
    assert lines[0].consumed_quantity == 30 and lines[0].consumed_unit == "GB"
    assert all(x.extras["estimate"] for x in lines) and lines[0].sub_account_id == "1234567"
    (req,) = fake.requests
    assert req.url.host == "api.eu.newrelic.com" and req.headers["api-key"] == "NRAK-test"
    query = json.loads(req.content)["query"]
    assert "account(id: 1234567)" in query and "NrMTDConsumption" in query
    since = int(datetime(2026, 10, 1, tzinfo=UTC).timestamp() * 1000)
    assert f"SINCE {since} UNTIL" in query


def test_errors_and_bad_settings() -> None:
    refused = Fake(answer([], [], [], errors=[{"message": "Invalid API key"}]))
    with pytest.raises(saas.ConnectorError, match="refused"):
        saas.newrelic_month(refused.client(), {"account_id": "1"}, {"user_key": "x"}, OCT)
    empty = Fake(answer([], [], []))
    assert saas.newrelic_month(empty.client(), {"account_id": "1"}, {"user_key": "x"}, OCT) == []
    for cfg in (
        {"account_id": "1 OR 1"},
        {"account_id": "1", "region": "APAC"},
        {"account_id": "1", "price_per_gb": "cheap"},
        {"account_id": "1", "price_per_gb": "-1"},
    ):
        with pytest.raises(saas.ConnectorError):
            saas.newrelic_month(empty.client(), cfg, {"user_key": "x"}, OCT)
    assert len(empty.requests) == 1  # bad settings never reach New Relic
