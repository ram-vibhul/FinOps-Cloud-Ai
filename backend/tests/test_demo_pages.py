"""The demo organisation shows realistic data on every page (1.12): each page's API
answers with something to look at, as a read-only visitor sees it."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ccradar.db import Database
from ccradar.db.base import utcnow
from tests.helpers import make_user, sign_in_client


@pytest.fixture
def visitor(client: TestClient, db: Database) -> TestClient:
    uid = make_user(db, "visitor@example.com")
    sign_in_client(client, db, uid)
    assert client.post("/api/v1/demo/join").status_code == 200
    return client


def _get(c: TestClient, url: str) -> Any:
    r = c.get(url)
    assert r.status_code == 200, (url, r.text[:300])
    return r.json()


def test_every_page_has_data(visitor: TestClient) -> None:
    c = visitor
    providers = {p["provider"] for p in _get(c, "/api/v1/costs/providers")["providers"]}
    assert {"aws", "azure", "gcp", "datadog", "snowflake", "openai", "github"} <= providers

    for url in (
        "/api/v1/budgets",
        "/api/v1/governance/policies",
        "/api/v1/governance/violations",
        "/api/v1/virtual-tags",
        "/api/v1/allocation/rules",
        "/api/v1/org/teams",
        "/api/v1/unit-metrics",
        "/api/v1/reports/schedules",
        "/api/v1/views",
        "/api/v1/dashboards",
        "/api/v1/reliability-targets",
        "/api/v1/findings/owner-rules",
        "/api/v1/commitments/purchases",
        "/api/v1/k8s/investigations",
        "/api/v1/costs/hourly",
    ):
        assert _get(c, url), url

    assert _get(c, "/api/v1/dashboards")[0]["widgets"]
    ai = _get(c, "/api/v1/ai/summary")
    assert ai["sdk"]["totals"]["cost_usd"] > 0
    assert ai["cloud_services"]
    assert _get(c, "/api/v1/costs/network/attribution")["items"]
    statuses = {f["status"] for f in _get(c, "/api/v1/findings?status=all")["items"]}
    assert {"open", "approved", "applied", "verified"} <= statuses

    last_month = (utcnow().date().replace(day=1) - timedelta(days=1)).replace(day=1)
    statement = _get(c, f"/api/v1/statements/{last_month:%Y-%m}")
    assert statement["closed"]
    bench = _get(c, "/api/v1/benchmarks")
    assert bench["sample"] is True
    assert bench["peers"]["metrics"]["waste_pct"]["median"] > 0

    metric = _get(c, "/api/v1/unit-metrics")[0]
    report = _get(c, f"/api/v1/unit-metrics/{metric['id']}/report")
    assert report


def test_demo_stays_read_only(visitor: TestClient) -> None:
    r = visitor.post("/api/v1/budgets", json={"name": "x", "amount": 1})
    assert r.status_code == 403
