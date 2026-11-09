from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient

from costlens.costs.ask import SCHEMA
from costlens.costs.ingest import CostLine, replace_billing_period
from costlens.db import Database
from tests.helpers import make_org, sign_in_client


class FakeQueryWriter:
    def __init__(self, answer: dict[str, Any] | None) -> None:
        self.answer = answer
        self.prompts: list[str] = []
        self.schemas: list[dict[str, Any]] = []

    def rewrite(self, facts: str, system: str | None = None) -> str | None:
        return None

    def structured(self, prompt: str, system: str, schema: dict[str, Any]) -> dict[str, Any] | None:
        self.prompts.append(prompt)
        self.schemas.append(schema)
        return self.answer


def answer(**over: Any) -> dict[str, Any]:
    base = {
        "understood": True,
        "explanation": "EC2 cost by region, month by month, for the last 3 months.",
        "days": 90,
        "start": None,
        "end": None,
        "metric": "effective",
        "granularity": "month",
        "group_by": ["region"],
        "filters": [{"dimension": "service", "values": ["Amazon Elastic Compute Cloud"]}],
        "chart": "bar",
    }
    return {**base, **over}


def setup(client: TestClient, db: Database, writer: Any, ai_on: bool = True) -> TestClient:
    client.app.state.ai = writer  # type: ignore[attr-defined]
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    lines = [
        CostLine(
            charge_date=date(2026, 10, d),
            provider="aws",
            service_name=svc,
            region="eu-west-1",
            billed_cost=Decimal("10"),
            effective_cost=Decimal("10"),
            billing_currency="USD",
            tags={"team": "payments"},
        )
        for d in range(1, 11)
        for svc in ("Amazon Elastic Compute Cloud", "Amazon Simple Storage Service")
    ]
    with db.session() as s:
        replace_billing_period(
            s,
            org_id=org_id,
            source_kind="t",
            source_key="t",
            billing_period=date(2026, 10, 1),
            lines=lines,
        )
    c = sign_in_client(client, db, owner, org_id)
    if ai_on:
        assert (
            c.put("/api/v1/org/alert-settings", json={"ai_explanations": True}).status_code == 200
        )
    return c


def test_sentence_becomes_checked_query_without_running(client: TestClient, db: Database) -> None:
    writer = FakeQueryWriter(answer())
    c = setup(client, db, writer)
    r = c.post("/api/v1/costs/ask", json={"question": "EC2 cost by region for 3 months"})
    assert r.status_code == 200, r.text
    out = r.json()
    q = out["query"]
    assert q["start"] == "2026-07-13" and q["end"] == "2026-10-10"  # 90 days back from latest data
    assert q["group_by"] == ["region"] and q["granularity"] == "month"
    assert q["filters"] == {"service": ["Amazon Elastic Compute Cloud"]}
    assert out["chart"] == "bar" and out["explanation"].startswith("EC2 cost")
    # The AI saw names to choose from, but no amounts, and a strict schema.
    prompt = writer.prompts[0]
    assert "Amazon Elastic Compute Cloud" in prompt and "eu-west-1" in prompt
    assert "team" in prompt and "Question: EC2 cost by region for 3 months" in prompt
    assert "10.0" not in prompt and "200" not in prompt
    assert writer.schemas[0] is SCHEMA and SCHEMA["additionalProperties"] is False
    # The proposed query runs as-is through the normal explorer.
    run = c.post("/api/v1/costs/explore", json=q)
    assert run.status_code == 200 and run.json()["total"] == 100


def test_exact_dates_and_tags(client: TestClient, db: Database) -> None:
    writer = FakeQueryWriter(
        answer(
            days=None,
            start="2026-10-01",
            end="2026-10-05",
            granularity="day",
            group_by=["tag:team", "service", "account"],
            filters=[],
            chart="line",
        )
    )
    c = setup(client, db, writer)
    q = c.post("/api/v1/costs/ask", json={"question": "spend by team first week of October"}).json()
    assert q["query"]["start"] == "2026-10-01" and q["query"]["end"] == "2026-10-05"
    assert q["query"]["group_by"] == ["tag:team", "service"]  # at most two
    assert q["chart"] == "line"


def test_bad_or_unhelpful_answers_are_refused(client: TestClient, db: Database) -> None:
    writer = FakeQueryWriter(answer(group_by=["DROP TABLE cost_daily"]))
    c = setup(client, db, writer)
    r = c.post("/api/v1/costs/ask", json={"question": "cost by something odd"})
    assert r.status_code == 400 and r.json()["code"] == "ai_bad_query"
    writer.answer = answer(start="2026-10-09", end="2026-10-01", days=None)
    assert (
        c.post("/api/v1/costs/ask", json={"question": "backwards"}).json()["code"] == "ai_bad_query"
    )
    writer.answer = answer(understood=False)
    assert c.post("/api/v1/costs/ask", json={"question": "tell me a joke"}).json()["code"] == (
        "ai_not_understood"
    )
    writer.answer = None
    assert c.post("/api/v1/costs/ask", json={"question": "anything"}).json()["code"] == "ai_failed"


def test_needs_ai_on_server_and_org(client: TestClient, db: Database) -> None:
    c = setup(client, db, FakeQueryWriter(answer()), ai_on=False)
    r = c.post("/api/v1/costs/ask", json={"question": "EC2 by region"})
    assert r.status_code == 409 and r.json()["code"] == "ai_off"
    client.app.state.ai = None  # type: ignore[attr-defined]
    r = c.post("/api/v1/costs/ask", json={"question": "EC2 by region"})
    assert r.status_code == 409 and r.json()["code"] == "ai_disabled"
