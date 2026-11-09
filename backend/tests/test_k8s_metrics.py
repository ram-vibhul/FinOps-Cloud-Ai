from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from costlens.accounts.roles import Role
from costlens.db import Database
from costlens.db.tenant import set_tenant
from costlens.k8s.promql import problem
from costlens.k8s.service import issue_token
from costlens.models import K8sCluster
from tests.helpers import add_to_org, make_org, sign_in_client


@pytest.mark.parametrize(
    ("q", "ok"),
    [
        ('sum by (queue) (rate(jobs_total{status="failed"}[5m]))', True),
        ("histogram_quantile(0.99, sum by (le) (rate(http_seconds_bucket[5m])))", True),
        ("max_over_time(queue_depth[1h:1m])", True),
        ('up{job="a(b"}', True),
        ("rate(x[2d])", False),
        ("sum(x", False),
        ('up{a="b}', False),
        ("up\n", False),
        ("x" * 501, False),
        ("rate(x[five])", False),
        ("", False),
    ],
)
def test_promql_rules(q: str, ok: bool) -> None:
    assert (problem(q) is None) is ok, problem(q)


def test_agent_copy_of_promql_rules_is_identical() -> None:
    from costlens_agent import promql as agent

    from costlens.k8s import promql as server

    for q in ("rate(x[2d])", "sum(x", "ok_metric", 'a{b="("}', "x[1h:5m]", "é"):
        assert agent.problem(q) == server.problem(q)
    assert (
        agent.MAX_LENGTH == server.MAX_LENGTH
        and agent.MAX_RANGE_SECONDS == server.MAX_RANGE_SECONDS
    )


def setup(client: TestClient, db: Database):
    org_id, owner = make_org(db, "Acme", "owner@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        c = K8sCluster(
            id=uuid.uuid4(), org_id=org_id, name="prod", token_hash="x" * 64, token_hint="x"
        )
        token = issue_token(c)
        s.add(c)
        cid = str(c.id)
    return org_id, owner, cid, token


def agent_post(client: TestClient, token: str, path: str, body: dict):
    client.cookies.clear()
    return client.post(
        f"/api/v1/agent/{path}", json=body, headers={"Authorization": f"Bearer {token}"}
    )


def test_queries_api_agent_flow_and_isolation(client: TestClient, db: Database) -> None:
    org_id, owner, cid, token = setup(client, db)
    viewer = add_to_org(db, org_id, "viewer@acme.com", Role.VIEWER)
    url = f"/api/v1/k8s/clusters/{cid}/metric-queries"
    v = sign_in_client(client, db, viewer, org_id)
    assert v.post(url, json={"name": "q", "promql": "up"}).status_code == 403
    c = sign_in_client(client, db, owner, org_id)
    bad = c.post(url, json={"name": "too long", "promql": "rate(x[30d])"})
    assert bad.status_code == 422 and "at most 1d" in bad.text
    q = c.post(
        url, json={"name": "Queue depth", "promql": "sum by (queue) (queue_depth)", "unit": "jobs"}
    ).json()
    for i in range(9):
        c.post(url, json={"name": f"q{i}", "promql": "up"})
    assert c.post(url, json={"name": "eleventh", "promql": "up"}).status_code == 409
    # The agent gets enabled queries in its config and reports values.
    config = agent_post(
        client,
        token,
        "hello",
        {
            "agent_version": "0.1.0",
            "usage_source": "prometheus",
            "prometheus_url": "http://prometheus-operated.monitoring.svc:9090",
            "prometheus_discovered": True,
        },
    ).json()
    assert (
        len(config["metric_queries"]) == 10
        and config["metric_queries"][0]["promql"] == "sum by (queue) (queue_depth)"
    )
    r = agent_post(
        client,
        token,
        "metrics",
        {
            "query_id": q["id"],
            "series": [
                {"labels": {"queue": "mail", "dsn": "postgres://u:Secret9@db/x"}, "value": 30},
                {"labels": {"queue": "orders"}, "value": 12},
            ],
        },
    )
    assert r.json() == {"stored": 2}
    agent_post(client, token, "metrics", {"query_id": q["id"], "ok": False, "error": "bad_data"})
    c = sign_in_client(client, db, owner, org_id)
    listed = next(x for x in c.get(url).json() if x["id"] == q["id"])
    assert listed["last_error"] == "bad_data" and listed["latest"][0]["value"] == 30
    assert "Secret9" not in str(listed["latest"])
    points = c.get(f"/api/v1/k8s/metric-queries/{q['id']}/points").json()
    assert points["unit"] == "jobs" and {s["labels"]["queue"] for s in points["series"]} == {
        "mail",
        "orders",
    }
    cluster = c.get("/api/v1/clusters").json()[0]
    assert (
        cluster["agent"]["prometheus_url"].startswith("http://prometheus-operated")
        and cluster["agent"]["prometheus_discovered"]
    )
    assert (
        c.patch(f"/api/v1/k8s/metric-queries/{q['id']}", json={"enabled": False}).json()["enabled"]
        is False
    )
    config = agent_post(client, token, "hello", {"agent_version": "0.1.0"}).json()
    assert q["id"] not in {x["id"] for x in config["metric_queries"]}
    # Another cluster's agent cannot write to this query; other orgs cannot read it.
    other_id, other = make_org(db, "Other", "o@other.com")
    with db.session() as s:
        set_tenant(s, org_id=other_id)
        oc = K8sCluster(
            id=uuid.uuid4(), org_id=other_id, name="x", token_hash="x" * 64, token_hint="x"
        )
        other_token = issue_token(oc)
        s.add(oc)
    assert (
        agent_post(client, other_token, "metrics", {"query_id": q["id"], "series": []}).status_code
        == 404
    )
    assert (
        sign_in_client(client, db, other, other_id)
        .get(f"/api/v1/k8s/metric-queries/{q['id']}/points")
        .status_code
        == 404
    )
    c = sign_in_client(client, db, owner, org_id)
    assert c.delete(f"/api/v1/k8s/metric-queries/{q['id']}").status_code == 204


def test_promql_test_task_validated(client: TestClient, db: Database) -> None:
    org_id, owner, cid, _ = setup(client, db)
    c = sign_in_client(client, db, owner, org_id)
    url = f"/api/v1/k8s/clusters/{cid}/tasks"
    assert (
        c.post(url, json={"kind": "promql", "params": {"promql": "rate(x[9d])"}}).status_code == 422
    )
    r = c.post(url, json={"kind": "promql", "params": {"promql": "sum(up)"}})
    assert r.status_code == 202 and r.json()["params"] == {"promql": "sum(up)"}
