from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ccradar.costs import ai_insights
from ccradar.db import Database
from ccradar.db.tenant import set_tenant
from ccradar.findings import service
from ccradar.models import AiUsageEvent, Finding
from tests.helpers import make_org, sign_in_client

NOW = datetime(2026, 11, 9, 12, tzinfo=UTC)


def ev(org_id, days_ago: float, **kw) -> AiUsageEvent:
    base = dict(
        org_id=org_id,
        at=NOW - timedelta(days=days_ago),
        provider="anthropic",
        model="claude-sonnet-5-5",
        input_tokens=1000,
        output_tokens=100,
        cache_read_tokens=0,
        cache_write_tokens=0,
        cost_usd=0.003,
        cost_source="price list",
        retry=False,
        status="ok",
    )
    return AiUsageEvent(**{**base, **kw})


def test_change_split_into_volume_and_price(db: Database) -> None:
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        # Before: 10 calls of 1,100 tokens at 0.0033 each. Now: 20 calls, and dearer per token.
        s.add_all(ev(org_id, 10, cost_usd=0.0033, team="search") for _ in range(10))
        s.add_all(
            ev(org_id, 2, cost_usd=0.0066, team="search", feature="answers") for _ in range(20)
        )
        s.flush()
        out = ai_insights.explain(s, org_id, days=7, now=NOW)
    assert out["before"] == pytest.approx(0.03, abs=0.01) and out["now"] == pytest.approx(
        0.13, abs=0.01
    )
    m = out["drivers"]["model"][0]
    assert m["key"] == "anthropic/claude-sonnet-5-5"
    assert m["volume_effect"] + m["price_effect"] == pytest.approx(m["change"], abs=0.01)
    assert m["volume_effect"] == pytest.approx(0.03, abs=0.01) and m["price_effect"] > 0
    assert out["drivers"]["feature"][0]["key"] == "answers"
    assert out["sentences"][0].startswith(
        "AI spend reported by your applications was 0.13 USD in the last 7 days, up"
    )
    assert any("more tokens" in x and "price per token" in x for x in out["sentences"])


def test_waste_kinds_and_findings(db: Database, monkeypatch) -> None:
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    fp = "ab" * 16
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        rows = []
        # 1) The same 50k-token prompt 300 times with no cache reads.
        rows += [
            ev(
                org_id,
                1 + i % 20,
                app="support",
                feature="triage",
                fingerprint=fp,
                input_tokens=50_000,
                output_tokens=2_000,
                cost_usd=0.13,
            )
            for i in range(300)
        ]
        # 2) Oversized context: 100k in, 50 out.
        rows += [
            ev(org_id, 3, app="search", input_tokens=100_000, output_tokens=50, cost_usd=0.2)
            for _ in range(200)
        ]
        # 3) Opus on tiny requests.
        rows += [
            ev(
                org_id,
                4,
                app="tagger",
                model="claude-opus-5-5",
                input_tokens=800,
                output_tokens=60,
                cost_usd=0.02,
            )
            for _ in range(2000)
        ]
        # 4) Retries.
        rows += [ev(org_id, 5, app="batch", retry=True, cost_usd=0.5) for _ in range(30)]
        # Haiku on tiny requests is already cheap: nothing to suggest.
        rows += [
            ev(
                org_id,
                4,
                app="tagger",
                model="claude-haiku-5-5",
                input_tokens=800,
                output_tokens=60,
                cost_usd=0.0001,
            )
            for _ in range(2000)
        ]
        s.add_all(rows)
        s.flush()
        items = {w.kind: w for w in ai_insights.waste(s, org_id, now=NOW)}
    assert set(items) == {"repeated_prompts", "oversized_context", "expensive_model", "retries"}
    rep = items["repeated_prompts"]
    # 299 repeats x 50k input tokens x $2/M x 90% (cache reads cost a tenth).
    assert rep.key == "support/triage" and rep.saving_monthly == pytest.approx(
        299 * 0.1 * 0.9, abs=0.5
    )
    assert (
        items["expensive_model"].title
        == "Try claude-sonnet-5-5 instead of claude-opus-5-5 in tagger"
    )
    assert items["retries"].saving_monthly == pytest.approx(15.0)
    assert items["oversized_context"].saving_monthly == pytest.approx(20.0)

    # The detector turns them into savings findings (category "ai").
    monkeypatch.setattr(ai_insights, "utcnow", lambda: NOW)
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        service.run(s, org_id, only=["ai.token_waste"])
        found = {
            f.evidence["kind"]: f
            for f in s.scalars(select(Finding).where(Finding.detector == "ai.token_waste"))
        }
    assert set(found) == set(items)
    assert found["repeated_prompts"].category == "ai" and found["repeated_prompts"].provider == "ai"
    assert found["repeated_prompts"].steps[0].startswith("Mark the repeated part")


def test_api(client: TestClient, db: Database) -> None:
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        now = datetime.now(UTC)
        s.add_all(
            AiUsageEvent(
                id=uuid.uuid4(), org_id=org_id, at=now - timedelta(days=1), provider="openai", model="gpt-4o",
                input_tokens=500, output_tokens=50, cost_usd=0.4, cost_source="reported", retry=True, status="error",
            )
            for _ in range(40)
        )  # fmt: skip
    c = sign_in_client(client, db, owner, org_id)
    ch = c.get("/api/v1/ai/changes", params={"days": 7}).json()
    assert ch["now"] == pytest.approx(16.0) and ch["drivers"]["model"][0]["key"] == "openai/gpt-4o"
    kinds = {w["kind"] for w in c.get("/api/v1/ai/waste").json()}
    assert {"retries", "expensive_model"} <= kinds
