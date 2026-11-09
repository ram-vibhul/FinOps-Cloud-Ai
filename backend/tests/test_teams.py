from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

from costlens.accounts.roles import Role
from costlens.db import Database
from tests.helpers import add_to_org, make_org, sign_in_client


def test_teams_members_and_my_teams(client: TestClient, db: Database) -> None:
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    dev = add_to_org(db, org_id, "dev@acme.com", Role.MEMBER)
    _, outsider = make_org(db, "Other", "x@other.com")
    c = sign_in_client(client, db, owner, org_id)
    bad = c.post("/api/v1/org/teams", json={"name": "Data", "members": [str(outsider)]})
    assert bad.json()["code"] == "not_a_member"
    t = c.post(
        "/api/v1/org/teams", json={"name": "Data", "cost_key": "data-eng", "members": [str(dev)]}
    ).json()
    assert t["cost_key"] == "data-eng" and [m["email"] for m in t["members"]] == ["dev@acme.com"]
    assert c.post("/api/v1/org/teams", json={"name": "Data"}).json()["code"] == "duplicate"
    t2 = c.patch(f"/api/v1/org/teams/{t['id']}", json={"members": [str(dev), str(owner)]}).json()
    assert len(t2["members"]) == 2
    m = sign_in_client(client, db, dev, org_id)
    assert m.get("/api/v1/me/teams").json() == [
        {"id": t["id"], "name": "Data", "cost_key": "data-eng"}
    ]
    assert m.post("/api/v1/org/teams", json={"name": "Mine"}).status_code == 403  # members cannot
    c = sign_in_client(client, db, owner, org_id)
    assert c.delete(f"/api/v1/org/teams/{t['id']}").status_code == 204
    assert c.get("/api/v1/org/teams").json() == []


class FakeDoH:
    def __init__(self) -> None:
        self.records: dict[str, list[str]] = {}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers["accept"] == "application/dns-json"
        name = request.url.params["name"]
        answers = [{"name": name, "type": 16, "data": f'"{v}"'} for v in self.records.get(name, [])]
        return httpx.Response(200, json={"Status": 0, "Answer": answers})


def test_domain_verification_by_dns_txt(client: TestClient, db: Database) -> None:
    fake = FakeDoH()
    client.app.state.http = httpx.Client(transport=httpx.MockTransport(fake))  # type: ignore[attr-defined]
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    c = sign_in_client(client, db, owner, org_id)
    for bad in ("gmail.com", "not a domain", "localhost"):
        assert c.post("/api/v1/org/domains", json={"domain": bad}).json()["code"] == "bad_domain"
    d = c.post("/api/v1/org/domains", json={"domain": "@Acme.COM."}).json()
    assert d["domain"] == "acme.com" and d["verified_at"] is None
    rec = d["record"]
    assert rec["name"] == "_costlens-verification.acme.com" and rec["type"] == "TXT"
    first = c.post(f"/api/v1/org/domains/{d['id']}/verify").json()
    assert first["verified_at"] is None and "not found" in first["last_error"]
    fake.records[rec["name"]] = ["v=spf1 -all", rec["value"]]
    ok = c.post(f"/api/v1/org/domains/{d['id']}/verify").json()
    assert ok["verified_at"] is not None and ok["last_error"] is None
    assert fake.requests[0].url.host == "cloudflare-dns.com"
    # Another organisation cannot verify the same domain.
    other_id, other = make_org(db, "Rival", "r@rival.com")
    o = sign_in_client(client, db, other, other_id)
    od = o.post("/api/v1/org/domains", json={"domain": "acme.com"}).json()
    fake.records["_costlens-verification.acme.com"].append(od["record"]["value"])
    assert o.post(f"/api/v1/org/domains/{od['id']}/verify").json()["code"] == "domain_taken"
