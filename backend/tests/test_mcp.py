from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient

from costlens.costs.ingest import CostLine, replace_billing_period
from costlens.db import Database
from costlens.db.base import utcnow
from tests.helpers import make_org, sign_in_client


def rpc(
    client: TestClient,
    key: str,
    method: str,
    params: dict[str, Any] | None = None,
    id_: int | None = 1,
):
    body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if id_ is not None:
        body["id"] = id_
    if params is not None:
        body["params"] = params
    return client.post(
        "/mcp",
        json=body,
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json, text/event-stream"},
    )


def setup(client: TestClient, db: Database) -> str:
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    today = utcnow().date()
    lines = [
        CostLine(
            charge_date=today - timedelta(days=d),
            provider="aws",
            service_name=svc,
            region="eu-west-1",
            billed_cost=Decimal(v),
            effective_cost=Decimal(v),
            billing_currency="USD",
        )
        for d in range(0, 10)
        for svc, v in (("Amazon EC2", "100"), ("Amazon S3", "10"))
    ]
    with db.session() as s:
        for month in sorted({x.charge_date.replace(day=1) for x in lines}):
            replace_billing_period(
                s,
                org_id=org_id,
                source_kind="t",
                source_key="t",
                billing_period=month,
                lines=[x for x in lines if x.charge_date.replace(day=1) == month],
            )
    c = sign_in_client(client, db, owner, org_id)
    key = c.post("/api/v1/org/api-keys", json={"name": "claude", "scope": "read"}).json()["key"]
    client.cookies.clear()
    return key


def test_handshake_tools_and_calls(client: TestClient, db: Database) -> None:
    key = setup(client, db)
    init = rpc(
        client,
        key,
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        },
    ).json()
    assert init["result"]["protocolVersion"] == "2025-06-18"
    assert (
        init["result"]["serverInfo"]["name"] == "costlens"
        and "read-only" in init["result"]["instructions"]
    )
    assert rpc(client, key, "notifications/initialized", id_=None).status_code == 202
    assert rpc(client, key, "ping").json()["result"] == {}
    tools = {t["name"] for t in rpc(client, key, "tools/list").json()["result"]["tools"]}
    assert tools == {
        "cost_summary",
        "explore_costs",
        "top_cost_changes",
        "list_savings",
        "list_anomalies",
        "budgets",
    }
    out = rpc(
        client,
        key,
        "tools/call",
        {"name": "explore_costs", "arguments": {"group_by": ["service"], "days": 10}},
    ).json()["result"]
    assert out["isError"] is False
    groups = out["structuredContent"]["groups"]
    assert groups[0]["key"] == "Amazon EC2" and groups[0]["cost"] == 1000
    summary = rpc(client, key, "tools/call", {"name": "cost_summary"}).json()["result"][
        "structuredContent"
    ]
    assert summary["currency"] == "USD" and summary["month_to_date"] > 0
    bad = rpc(
        client,
        key,
        "tools/call",
        {"name": "explore_costs", "arguments": {"group_by": ["DROP TABLE"]}},
    ).json()["result"]
    assert bad["isError"] is True and "Invalid query" in bad["content"][0]["text"]
    assert (
        rpc(client, key, "tools/call", {"name": "delete_everything"}).json()["result"]["isError"]
        is True
    )
    assert rpc(client, key, "resources/list").json()["error"]["code"] == -32601
    assert rpc(client, key, "tools/call", {"name": "list_savings"}).json()["result"][
        "structuredContent"
    ] == {"items": []}


def test_needs_a_key(client: TestClient, db: Database) -> None:
    setup(client, db)
    assert rpc(client, "clk_wrong", "tools/list").status_code == 401
    assert (
        client.post(
            "/mcp", content=b"not json", headers={"Authorization": "Bearer clk_wrong"}
        ).status_code
        == 401
    )


def test_dates_are_iso(client: TestClient, db: Database) -> None:
    key = setup(client, db)
    out = rpc(
        client, key, "tools/call", {"name": "top_cost_changes", "arguments": {"days": 3}}
    ).json()["result"]
    start, _ = out["structuredContent"]["current_period"]
    assert date.fromisoformat(start)


def test_create_investigation_only_with_permission(client: TestClient, db: Database) -> None:
    import uuid as _uuid

    from sqlalchemy import select

    from costlens.db.tenant import set_tenant
    from costlens.models import AuditEvent, K8sCluster, K8sInvestigation

    org_id, owner = make_org(db, "Acme", "o@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        s.add(
            K8sCluster(
                id=_uuid.uuid4(), org_id=org_id, name="prod", token_hash="x" * 64, token_hint="x"
            )
        )
    c = sign_in_client(client, db, owner, org_id)
    plain = c.post("/api/v1/org/api-keys", json={"name": "chat", "scope": "read"}).json()["key"]
    inv_key = c.post(
        "/api/v1/org/api-keys",
        json={"name": "oncall bot", "scope": "read", "can_investigate": True},
    ).json()
    assert inv_key["can_investigate"] is True
    client.cookies.clear()
    args = {
        "name": "create_investigation",
        "arguments": {"cluster": "prod", "namespace": "payments", "name": "api"},
    }
    # Without the permission the tool is neither listed nor callable.
    assert "create_investigation" not in {
        t["name"] for t in rpc(client, plain, "tools/list").json()["result"]["tools"]
    }
    assert rpc(client, plain, "tools/call", args).json()["result"]["isError"] is True
    # With it: listed, works, saved as an MCP investigation, audited as the key.
    key = inv_key["key"]
    assert "create_investigation" in {
        t["name"] for t in rpc(client, key, "tools/list").json()["result"]["tools"]
    }
    out = rpc(client, key, "tools/call", args).json()["result"]
    assert out["isError"] is False and out["structuredContent"]["link"].startswith(
        "/kubernetes/investigations/"
    )
    missing = rpc(
        client, key, "tools/call", {**args, "arguments": {**args["arguments"], "cluster": "nope"}}
    ).json()["result"]
    assert missing["isError"] is True and "No cluster" in missing["content"][0]["text"]
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        inv = s.scalars(select(K8sInvestigation)).one()
        assert inv.trigger == "mcp" and inv.namespace == "payments"
        labels = [
            e.actor_label
            for e in s.scalars(
                select(AuditEvent).where(AuditEvent.target_type == "k8s_investigation")
            )
        ]
    assert labels and labels[0].startswith("API key oncall bot")
    # The REST endpoint still needs a write key: the permission only opens the MCP tool.
    r = client.post(
        "/api/v1/k8s/investigations",
        json={"cluster_id": str(inv.cluster_id)},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert r.status_code == 403
