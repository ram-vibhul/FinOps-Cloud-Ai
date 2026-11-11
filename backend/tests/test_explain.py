from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select

from ccradar.costs.anomalies import detect
from ccradar.costs.explain import explain
from ccradar.costs.ingest import CostLine
from ccradar.db import Database
from ccradar.db.tenant import set_tenant
from ccradar.models import ChangeEvent, CostAnomaly, Organization
from tests.helpers import make_org, sign_in_client
from tests.test_anomalies import END, load, series_lines


def spike_lines():
    """Redshift spike on END driven by a new usage type in account 2."""
    lines = series_lines()
    lines.append(
        CostLine(
            charge_date=END,
            provider="aws",
            service_name="Redshift",
            sub_account_id="2",
            usage_type="RA3:4xlarge",
            resource_id="cluster-analytics",
            billed_cost=Decimal(900),
            effective_cost=Decimal(900),
            billing_currency="USD",
        )
    )
    return lines


def test_template_names_amount_driver_and_change(db: Database) -> None:
    org_id, _ = make_org(db, "Acme", "owner@acme.com")
    load(db, org_id, spike_lines())
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        s.add(
            ChangeEvent(
                org_id=org_id,
                occurred_at=datetime.combine(END - timedelta(days=1), datetime.min.time(), UTC),
                source="github",
                kind="deploy",
                title="Resize analytics cluster",
                service="Redshift",
                external_id="x1",
            )
        )
    with db.session() as s:
        detect(s, org_id)
        set_tenant(s, org_id=org_id)
        a = s.scalars(select(CostAnomaly).where(CostAnomaly.dimension == "service")).one()
        out = explain(s, a)
    text = out["text"]
    assert text.startswith("Redshift cost 1,100.00 USD on 14 Nov, about 900.00 USD more")
    assert "Most of the increase came from account 2 (+900.00 USD a day)" in text
    assert "usage type RA3:4xlarge" in text
    assert '"Resize analytics cluster" (github, 13 Nov)' in text
    assert {"by": "resource_id", "value": "cluster-analytics"}.items() <= next(
        d for d in out["drivers"] if d["by"] == "resource_id"
    ).items()
    assert out["worded_by"] == "template" and out["changes"][0]["title"].startswith("Resize")


class FakeWriter:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def rewrite(self, facts: str, system: str | None = None) -> str | None:
        self.calls.append(facts)
        return "Redshift jumped because of a new RA3 cluster."


def test_api_template_by_default_ai_only_when_org_opts_in(client: TestClient, db: Database) -> None:
    writer = FakeWriter()
    client.app.state.ai = writer  # type: ignore[attr-defined]
    org_id, owner = make_org(db, "Acme", "owner@acme.com")
    load(db, org_id, spike_lines())
    with db.session() as s:
        detect(s, org_id)
    c = sign_in_client(client, db, owner, org_id)
    aid = next(a["id"] for a in c.get("/api/v1/costs/anomalies").json() if a["key"] == "Redshift")
    first = c.get(f"/api/v1/costs/anomalies/{aid}/explanation").json()
    assert first["worded_by"] == "template" and writer.calls == []
    assert c.put("/api/v1/org/alert-settings", json={"ai_explanations": True}).status_code == 200
    second = c.get(f"/api/v1/costs/anomalies/{aid}/explanation").json()
    assert second["worded_by"] == "ai" and second["text"].startswith("Redshift jumped")
    assert second["template_text"] == first["text"]
    assert "sub_account_id=2" in writer.calls[0]


def test_ai_failure_falls_back_to_template(client: TestClient, db: Database) -> None:
    class Broken:
        def rewrite(self, facts: str, system: str | None = None) -> str | None:
            return None

    client.app.state.ai = Broken()  # type: ignore[attr-defined]
    org_id, owner = make_org(db, "Acme", "owner@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        org = s.get(Organization, org_id)
        assert org is not None
        org.settings = {"alerts": {"ai_explanations": True}}
    load(db, org_id, spike_lines())
    with db.session() as s:
        detect(s, org_id)
    c = sign_in_client(client, db, owner, org_id)
    aid = c.get("/api/v1/costs/anomalies").json()[0]["id"]
    assert c.get(f"/api/v1/costs/anomalies/{aid}/explanation").json()["worded_by"] == "template"
    assert c.get(f"/api/v1/costs/anomalies/{org_id}/explanation").status_code == 404


def test_alert_email_includes_explanation(db: Database) -> None:
    from ccradar.costs.alerts import pending_alert

    org_id, _ = make_org(db, "Acme", "owner@acme.com")
    load(db, org_id, spike_lines())
    with db.session() as s:
        detect(s, org_id)
        emails = pending_alert(s, org_id, "https://x")
    assert "Most of the increase came from account 2" in emails[0].text


def test_anthropic_writer_with_fake_client(settings) -> None:
    from types import SimpleNamespace

    import anthropic
    import httpx2
    from pydantic import SecretStr

    from ccradar.ai.writer import AnthropicWriter

    writer = AnthropicWriter(
        settings.model_copy(update={"anthropic_api_key": SecretStr("test-key")})
    )
    sent: dict = {}

    def reply(stop: str, *texts: str):
        blocks = [SimpleNamespace(type="thinking", thinking="")] + [
            SimpleNamespace(type="text", text=t) for t in texts
        ]
        return SimpleNamespace(stop_reason=stop, content=blocks)

    def create(**kwargs):
        sent.update(kwargs)
        return outcome()

    writer._client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))  # type: ignore[assignment]
    outcome = lambda: reply("end_turn", "Plain ", "words.")  # noqa: E731
    assert writer.rewrite("facts") == "Plain words."
    assert sent["model"] == settings.ai_model and sent["fallbacks"] == "default"
    outcome = lambda: reply("refusal")  # noqa: E731
    assert writer.rewrite("facts") is None

    def fail():
        request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
        raise anthropic.APIConnectionError(request=request)

    outcome = fail
    assert writer.rewrite("facts") is None
    assert writer.structured("q", "sys", {"type": "object"}) is None

    outcome = lambda: reply("end_turn", '{"a": ', "1}")  # noqa: E731
    assert writer.structured("q", "sys", {"type": "object"}) == {"a": 1}
    assert sent["output_config"]["format"] == {"type": "json_schema", "schema": {"type": "object"}}
    outcome = lambda: reply("end_turn", "not json")  # noqa: E731
    assert writer.structured("q", "sys", {}) is None
    outcome = lambda: reply("max_tokens", '{"a"')  # noqa: E731
    assert writer.structured("q", "sys", {}) is None


def test_demo_redshift_spike_is_explained(db: Database) -> None:
    from ccradar.demo.seed import ensure_demo, load_all_seeders

    load_all_seeders()
    with db.session() as s:
        org_id = ensure_demo(s).id
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        a = s.scalars(select(CostAnomaly).where(CostAnomaly.key == "Amazon Redshift")).first()
        assert a is not None
        out = explain(s, a)
    print(out["text"])
    assert "Amazon Redshift" in out["text"] and "of the increase came from" in out["text"]
