from __future__ import annotations

import base64
import json
from typing import Any

import httpx
from fastapi.testclient import TestClient

from ccradar.accounts.roles import Role
from ccradar.db import Database
from tests.helpers import add_to_org, make_org, sign_in_client
from tests.test_findings_workflow import make_finding


class FakeServices:
    """One fake for Jira, ServiceNow, GitHub and GitLab: records requests, answers like them."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.fail: int | None = None

    def body(self, i: int) -> dict[str, Any]:
        return json.loads(self.requests[i].content)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail:
            return httpx.Response(self.fail, text="no permission")
        url, method = str(request.url), request.method
        if url.endswith("/rest/api/3/issue"):
            return httpx.Response(201, json={"id": "10001", "key": "OPS-42"})
        if "/api/now/table/" in url:
            return httpx.Response(
                201, json={"result": {"sys_id": "abc123", "number": "INC0010001"}}
            )
        if "/git/ref/heads/" in url:
            return httpx.Response(200, json={"object": {"sha": "basesha"}})
        if url.endswith("/git/refs") or "/contents/" in url:
            return httpx.Response(201, json={})
        if url.endswith("/pulls"):
            return httpx.Response(
                201, json={"number": 7, "html_url": "https://github.com/acme/infra/pull/7"}
            )
        if url.endswith("/repository/commits"):
            return httpx.Response(201, json={"id": "c1"})
        if url.endswith("/merge_requests"):
            return httpx.Response(
                201, json={"iid": 3, "web_url": "https://gitlab.com/acme/infra/-/merge_requests/3"}
            )
        return httpx.Response(404, text=f"unexpected {method} {url}")


def setup(client: TestClient, db: Database):
    fake = FakeServices()
    client.app.state.http = httpx.Client(transport=httpx.MockTransport(fake))  # type: ignore[attr-defined]
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    fid = make_finding(
        db,
        org_id,
        steps=["Snapshot the volume", "Delete it"],
        commands=["aws ec2 delete-volume --volume-id vol-x"],
        region="eu-west-1",
    )
    return fake, org_id, owner, fid, sign_in_client(client, db, owner, org_id)


def test_jira_ticket_and_servicenow_record(client: TestClient, db: Database) -> None:
    fake, _, _, fid, c = setup(client, db)
    r = c.post(
        "/api/v1/org/work-integrations",
        json={
            "kind": "jira",
            "name": "Ops Jira",
            "base_url": "https://acme.atlassian.net/",
            "username": "bot@acme.com",
            "token": "jira-secret-token",
            "config": {"project_key": "OPS", "issue_type": "Task"},
        },
    )
    assert r.status_code == 201, r.text
    jira = r.json()
    assert jira["token_hint"] == "…oken" and "token" not in jira
    assert jira["base_url"] == "https://acme.atlassian.net"
    link = c.post(f"/api/v1/findings/{fid}/links", json={"integration_id": jira["id"]})
    assert link.status_code == 201, link.text
    assert link.json()["external_id"] == "OPS-42"
    assert link.json()["url"] == "https://acme.atlassian.net/browse/OPS-42"
    req = fake.requests[0]
    assert (
        req.headers["authorization"]
        == "Basic " + base64.b64encode(b"bot@acme.com:jira-secret-token").decode()
    )
    fields = fake.body(0)["fields"]
    assert (
        fields["project"] == {"key": "OPS"} and fields["summary"] == "[CloudCostRadar] Delete vol-x"
    )
    text = json.dumps(fields["description"])
    assert "300.00 USD a month" in text and "aws ec2 delete-volume" in text and "eu-west-1" in text

    sn = c.post(
        "/api/v1/org/work-integrations",
        json={
            "kind": "servicenow",
            "name": "ServiceNow",
            "base_url": "https://acme.service-now.com",
            "username": "ccradar",
            "token": "pw-1234",
            "config": {"table": "incident"},
        },
    ).json()
    out = c.post(f"/api/v1/findings/{fid}/links", json={"integration_id": sn["id"]}).json()
    assert out["external_id"] == "INC0010001" and out["kind"] == "ticket"
    assert out["url"] == (
        "https://acme.service-now.com/nav_to.do?uri=incident.do%3Fsys_id%3Dabc123"
    )
    assert fake.body(1)["short_description"] == "[CloudCostRadar] Delete vol-x"
    links = c.get(f"/api/v1/findings/{fid}/links").json()
    assert [x["provider"] for x in links] == ["jira", "servicenow"]
    history = c.get(f"/api/v1/findings/{fid}/history").json()
    assert any("Ticket OPS-42 opened in Ops Jira" in h["text"] for h in history), history


def test_github_and_gitlab_pull_requests(client: TestClient, db: Database) -> None:
    fake, _, _, fid, c = setup(client, db)
    gh = c.post(
        "/api/v1/org/work-integrations",
        json={
            "kind": "github",
            "name": "infra repo",
            "token": "ghp_test",
            "config": {"repo": "acme/infra", "base_branch": "main"},
        },
    ).json()
    assert gh["base_url"] == "https://api.github.com"
    out = c.post(f"/api/v1/findings/{fid}/links", json={"integration_id": gh["id"]}).json()
    assert out == {**out, "kind": "pull_request", "external_id": "7"}
    calls = [(r.method, r.url.path) for r in fake.requests]
    assert calls[0] == ("GET", "/repos/acme/infra/git/ref/heads/main")
    assert calls[1] == ("POST", "/repos/acme/infra/git/refs")
    assert calls[2][0] == "PUT" and calls[2][1].startswith(
        "/repos/acme/infra/contents/ccradar/fixes/"
    )
    assert calls[3] == ("POST", "/repos/acme/infra/pulls")
    branch = fake.body(1)["ref"].removeprefix("refs/heads/")
    assert branch.startswith("ccradar/delete-vol-x-") and fake.body(1)["sha"] == "basesha"
    plan = base64.b64decode(fake.body(2)["content"]).decode()
    assert plan.startswith("# Delete vol-x") and "1. Snapshot the volume" in plan
    assert fake.body(3) == {**fake.body(3), "head": branch, "base": "main"}
    assert fake.requests[0].headers["authorization"] == "Bearer ghp_test"

    gl = c.post(
        "/api/v1/org/work-integrations",
        json={
            "kind": "gitlab",
            "name": "gitlab",
            "token": "glpat-test",
            "config": {"project": "acme/infra"},
        },
    ).json()
    n = len(fake.requests)
    out = c.post(f"/api/v1/findings/{fid}/links", json={"integration_id": gl["id"]}).json()
    assert out["external_id"] == "3" and out["url"].endswith("/merge_requests/3")
    commit, mr = fake.requests[n], fake.requests[n + 1]
    assert commit.url.raw_path.decode().startswith(
        "/api/v4/projects/acme%2Finfra/repository/commits"
    )
    assert commit.headers["private-token"] == "glpat-test"
    assert json.loads(commit.content)["start_branch"] == "main"
    assert json.loads(mr.content)["target_branch"] == "main"


def test_validation_permissions_and_failures(client: TestClient, db: Database) -> None:
    fake, org_id, owner, fid, c = setup(client, db)
    bad = [
        {
            "kind": "jira",
            "name": "x",
            "base_url": "https://a.atlassian.net",
            "username": "u",
            "token": "tttt",
        },
        {"kind": "github", "name": "x", "token": "tttt", "config": {"repo": "not a repo"}},
        {
            "kind": "jira",
            "name": "x",
            "base_url": "https://jira.internal",
            "username": "u",
            "token": "tttt",
            "config": {"project_key": "OPS"},
        },
        {
            "kind": "jira",
            "name": "x",
            "base_url": "http://acme.atlassian.net",
            "username": "u",
            "token": "tttt",
            "config": {"project_key": "OPS"},
        },
    ]
    codes = [c.post("/api/v1/org/work-integrations", json=b).status_code for b in bad]
    assert codes == [422, 422, 400, 400]
    member = add_to_org(db, org_id, "dev@acme.com", Role.MEMBER)
    gh = c.post(
        "/api/v1/org/work-integrations",
        json={
            "kind": "github",
            "name": "infra",
            "token": "ghp_x",
            "config": {"repo": "acme/infra"},
        },
    ).json()
    m = sign_in_client(client, db, member, org_id)
    assert (
        m.post(
            "/api/v1/org/work-integrations",
            json={"kind": "github", "name": "y", "token": "tttt", "config": {"repo": "a/b"}},
        ).status_code
        == 403
    )
    fake.fail = 403
    r = m.post(f"/api/v1/findings/{fid}/links", json={"integration_id": gh["id"]})
    assert r.status_code == 502 and r.json()["code"] == "integration_failed"
    assert "403" in r.json()["detail"]
    fake.fail = None
    c = sign_in_client(client, db, owner, org_id)  # the test client holds one session at a time
    c.patch(f"/api/v1/org/work-integrations/{gh['id']}", json={"enabled": False})
    r = c.post(f"/api/v1/findings/{fid}/links", json={"integration_id": gh["id"]})
    assert r.json().get("code") == "integration_disabled", r.text
    assert c.delete(f"/api/v1/org/work-integrations/{gh['id']}").status_code == 204
    assert c.get("/api/v1/org/work-integrations").json() == []
