"""Cursor paging on the list APIs that grow (11.17)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ccradar.api import paging
from ccradar.core.errors import AppError
from ccradar.db import Database
from ccradar.db.tenant import set_tenant
from ccradar.models import CostAnomaly
from tests.helpers import make_org, sign_in_client


def walk(c: TestClient, path: str, **params) -> list[list[str]]:
    pages, cursor = [], None
    while True:
        r = c.get(path, params={**params, **({"cursor": cursor} if cursor else {})})
        assert r.status_code == 200, r.text
        pages.append([x["id"] for x in r.json()])
        cursor = r.headers.get(paging.HEADER)
        if cursor is None:
            return pages


def test_cursor_round_trip_and_bad_cursors() -> None:
    values = [datetime(2026, 11, 1, 9, tzinfo=UTC), date(2026, 11, 2), 12.5, uuid.uuid4()]
    assert paging.decode(paging.encode(values), 4) == values
    for bad in ("x", paging.encode(values), "e30"):  # not base64 JSON, wrong size, {}
        with pytest.raises(AppError):
            paging.decode(bad, 2)


def test_changes_page_without_gaps_or_repeats(client: TestClient, db: Database) -> None:
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    c = sign_in_client(client, db, owner, org_id)
    base = datetime.now(UTC) - timedelta(days=1)
    made = set()
    for i in range(7):
        when = base + timedelta(minutes=i // 2)  # pairs share a time: the id breaks ties
        r = c.post("/api/v1/changes", json={"title": f"c{i}", "occurred_at": when.isoformat()})
        made.add(r.json()["id"])
    pages = walk(c, "/api/v1/changes", limit=2)
    assert [len(p) for p in pages] == [2, 2, 2, 1]
    flat = [i for p in pages for i in p]
    assert len(flat) == len(set(flat)) and set(flat) == made
    assert flat == [x["id"] for x in c.get("/api/v1/changes").json()]  # same order unpaged
    bad = c.get("/api/v1/changes", params={"cursor": "nonsense"})
    assert bad.status_code == 400 and bad.json()["code"] == "invalid_cursor"


def test_anomalies_page_on_day_impact_and_id(client: TestClient, db: Database) -> None:
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        for i in range(5):
            s.add(
                CostAnomaly(
                    org_id=org_id,
                    day=date(2026, 11, 1),
                    last_day=date(2026, 11, 1 + i // 2),
                    dimension="service",
                    key=f"S{i}",
                    currency="USD",
                    actual=10,
                    expected=1,
                    upper=2,
                    impact=float(i % 2),
                    score=5,
                )
            )
    c = sign_in_client(client, db, owner, org_id)
    pages = walk(c, "/api/v1/costs/anomalies", limit=2)
    flat = [i for p in pages for i in p]
    assert [len(p) for p in pages] == [2, 2, 1] and len(set(flat)) == 5
    assert flat == [x["id"] for x in c.get("/api/v1/costs/anomalies").json()]
