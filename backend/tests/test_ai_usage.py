from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from costlens.costs import ai_usage
from costlens.costs.ingest import CostLine, replace_billing_period
from costlens.db import Database
from costlens.db.base import utcnow
from costlens.db.tenant import set_tenant
from costlens.models import AiUsageEvent
from tests.helpers import make_org, sign_in_client

SDK = Path(__file__).resolve().parents[2] / "sdk" / "python" / "src"
PROMPT = "Draft a reply to Priya about invoice 4471 and the overdue amount of 9,800."


def _event(**kw):
    base = {
        "at": utcnow().isoformat(),
        "provider": "anthropic",
        "model": "claude-sonnet-5-5",
        "input_tokens": 10,
    }
    return {**base, **kw}


def test_pricing_prefixes_cache_and_overrides() -> None:
    e = ai_usage.Event.model_validate(
        _event(
            input_tokens=1_000_000,
            output_tokens=100_000,
            cache_read_tokens=2_000_000,
            cache_write_tokens=0,
        )
    )
    prices = ai_usage.price_list(None)
    # Sonnet 5.5: 2 in, 10 out, 0.20 cache read per million.
    assert ai_usage.price(prices, "claude-sonnet-5-5", e) == pytest.approx(2.0 + 1.0 + 0.4)
    assert ai_usage.price(prices, "us.anthropic.claude-haiku-4-5", e) == pytest.approx(
        1.0 + 0.5 + 0.2
    )  # cache read 10%
    assert ai_usage.price(prices, "gpt-4o-mini-2024-07-18", e) == pytest.approx(0.15 + 0.06 + 0.03)
    assert ai_usage.price(prices, "some-new-model", e) is None


def test_ingest_privacy_and_summary(client: TestClient, db: Database) -> None:
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    c = sign_in_client(client, db, owner, org_id)
    created = c.post("/api/v1/org/ai-usage-keys", json={"name": "checkout service"}).json()
    key = created["key"]
    assert key.startswith("clai_") and created["key_hint"] == key[-6:]
    assert c.get("/api/v1/org/ai-usage-keys").json()[0]["key"] is None  # shown once

    def post(events, token=key):
        return client.post(
            "/api/v1/ingest/ai-usage",
            json={"events": events},
            headers={"Authorization": f"Bearer {token}"},
        )

    # Privacy: no field can hold text. Unknown fields, spaces in names and non-hex
    # fingerprints are refused.
    assert post([_event(prompt=PROMPT)]).status_code == 422
    assert post([_event(feature="Draft a reply to Priya")]).status_code == 422
    assert post([_event(fingerprint="not-a-hash with words")]).status_code == 422
    assert post([_event()], token="clai_wrong").status_code == 401
    assert post([_event()], token="").status_code == 401

    # The real SDK, posting through the API.
    sys.path.insert(0, str(SDK))
    try:
        from costlens_ai import Usage
    finally:
        sys.path.remove(str(SDK))

    def transport(url: str, body: bytes, headers: dict[str, str]) -> int:
        assert url.endswith("/api/v1/ingest/ai-usage")
        return client.post(
            url.replace("https://costlens.example", ""), content=body, headers=headers
        ).status_code

    sdk = Usage(
        key,
        "https://costlens.example",
        app="checkout",
        team="payments",
        salt=b"s",
        transport=transport,
    )
    for _ in range(3):
        sdk.record(
            "anthropic",
            "claude-sonnet-5-5",
            input_tokens=1_000_000,
            output_tokens=100_000,
            feature="emails",
            prompt=PROMPT,
        )
    sdk.record("openai", "gpt-4o", input_tokens=1000, cost_usd=0.5, feature="search", retry=True)
    sdk.record("acme", "home-grown-model", input_tokens=500, feature="search")
    assert sdk.flush() is True

    with db.session() as s:
        set_tenant(s, org_id=org_id)
        events = s.scalars(select(AiUsageEvent)).all()
        stored = " ".join(str(v) for e in events for v in vars(e).values())
    assert len(events) == 5
    for secret in ("Priya", "invoice", "4471", "9,800", "Draft"):
        assert secret not in stored
    fps = {e.fingerprint for e in events if e.fingerprint}
    assert len(fps) == 1  # the same prompt three times -> one fingerprint
    assert {e.cost_source for e in events} == {"price list", "reported", "unknown"}

    # Cloud bill AI services next to SDK usage.
    with db.session() as s:
        replace_billing_period(
            s,
            org_id=org_id,
            source_kind="t",
            source_key="t",
            billing_period=utcnow().date().replace(day=1),
            lines=[
                CostLine(
                    charge_date=utcnow().date(),
                    provider="aws",
                    service_name=svc,
                    billed_cost=Decimal(v),
                    effective_cost=Decimal(v),
                    billing_currency="USD",
                )
                for svc, v in (
                    ("Amazon Bedrock", "40"),
                    ("Amazon EC2", "500"),
                    ("Amazon SageMaker", "12"),
                )
            ],
        )
    out = c.get("/api/v1/ai/summary", params={"days": 7}).json()
    assert [r["service"] for r in out["cloud_services"]] == ["Amazon Bedrock", "Amazon SageMaker"]
    t = out["sdk"]["totals"]
    assert t["requests"] == 5 and t["retries"] == 1 and t["unpriced_requests"] == 1
    assert t["cost_usd"] == pytest.approx(3 * 3.0 + 0.5)
    assert out["sdk"]["by_model"][0] == {
        **out["sdk"]["by_model"][0],
        "key": "anthropic/claude-sonnet-5-5",
        "requests": 3,
    }
    assert {r["key"] for r in out["sdk"]["by_feature"]} == {"emails", "search"}
    assert out["sdk"]["by_team"][0]["key"] == "payments"

    # Price overrides and key revocation.
    assert (
        c.put("/api/v1/ai/prices/home-grown-model", json={"input": 1.0, "output": 2.0}).status_code
        == 200
    )
    listed = {m["model"]: m for m in c.get("/api/v1/ai/prices").json()["models"]}
    assert (
        listed["home-grown-model"]["custom"] is True and listed["claude-opus-5-5"]["input"] == 4.0
    )
    assert (
        c.put("/api/v1/ai/prices/bad name", json={"input": 1, "output": 1}).json()["code"]
        == "bad_model"
    )
    assert c.delete(f"/api/v1/org/ai-usage-keys/{created['id']}").status_code == 204
    assert post([_event()]).status_code == 401


def test_future_timestamps_are_clamped(db: Database) -> None:
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        key, _ = ai_usage.issue_key(s, org_id, "k")
        future = datetime.combine(
            date.today() + timedelta(days=30), datetime.min.time(), tzinfo=utcnow().tzinfo
        )
        ai_usage.ingest(
            s, key, ai_usage.Batch.model_validate({"events": [_event(at=future.isoformat())]})
        )
        e = s.scalars(select(AiUsageEvent)).one()
        at = e.at if e.at.tzinfo else e.at.replace(tzinfo=utcnow().tzinfo)  # SQLite drops the zone
        assert at <= utcnow() + timedelta(minutes=6)
