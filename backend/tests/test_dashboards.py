"""Custom dashboards (11.24): named pages of saved explorer views."""

from __future__ import annotations

from fastapi.testclient import TestClient

from ccradar.accounts.roles import Role
from ccradar.db import Database
from tests.helpers import add_to_org, make_org, sign_in_client


def test_dashboards_of_saved_views(client: TestClient, db: Database) -> None:
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    other = add_to_org(db, org_id, "m@acme.com", Role.MEMBER)
    o = sign_in_client(client, db, owner, org_id)
    shared = o.post(
        "/api/v1/views",
        json={"name": "By service", "shared": True, "query": {"group_by": ["service"], "days": 30}},
    ).json()
    private = o.post(
        "/api/v1/views",
        json={"name": "Mine", "shared": False, "query": {"group_by": ["region"], "days": 7}},
    ).json()
    made = o.post(
        "/api/v1/dashboards",
        json={
            "name": "Weekly",
            "widgets": [
                {"view_id": shared["id"], "chart": "line"},
                {"view_id": private["id"], "title": "Regions"},
            ],
        },
    )
    assert made.status_code == 201
    board = made.json()
    assert [w["title"] for w in board["widgets"]] == ["By service", "Regions"]
    assert board["widgets"][0]["query"]["group_by"] == ["service"]
    assert [v["name"] for v in o.get("/api/v1/views").json()] == [
        "By service",
        "Mine",
    ]  # not the board

    m = sign_in_client(client, db, other, org_id)
    (seen,) = m.get("/api/v1/dashboards").json()
    assert [w["title"] for w in seen["widgets"]] == ["By service"]  # the private view is left out
    bad = m.post("/api/v1/dashboards", json={"name": "x", "widgets": [{"view_id": private["id"]}]})
    assert bad.json()["code"] == "invalid_view"
    assert m.put(f"/api/v1/dashboards/{board['id']}", json={"name": "Mine now"}).status_code == 403
    o = sign_in_client(client, db, owner, org_id)  # one test client: sign the owner in again
    assert o.delete(f"/api/v1/dashboards/{board['id']}").status_code == 204
    assert o.get("/api/v1/dashboards").json() == []
