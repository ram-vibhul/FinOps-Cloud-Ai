from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ccradar.accounts.roles import Role
from ccradar.db import Database
from ccradar.db.base import utcnow
from ccradar.db.tenant import set_tenant
from ccradar.findings import registry, service
from ccradar.findings.registry import FindingIn, Level
from ccradar.models import Finding
from tests.helpers import add_to_org, make_org, sign_in_client

CURRENT: dict[str, list[FindingIn] | Exception] = {}


@pytest.fixture
def fake_detectors():
    """Two test detectors whose output each test controls."""
    registry.load_all()
    saved = dict(registry.DETECTORS)
    registry.DETECTORS.clear()

    def make(name: str):
        def detect(db, org_id, currency):
            out = CURRENT.get(name, [])
            if isinstance(out, Exception):
                raise out
            return out

        registry.DETECTORS[name] = registry.Detector(name, "waste", name, detect)

    make("t.volumes")
    make("t.gp3")
    yield
    registry.DETECTORS.clear()
    registry.DETECTORS.update(saved)
    CURRENT.clear()


def item(
    key: str,
    saving: float,
    *,
    resource: str | None = None,
    risk: Level = "low",
    effort: Level = "low",
) -> FindingIn:
    return FindingIn(
        key=key,
        title=f"Fix {key}",
        savings_monthly=saving,
        currency="USD",
        resource_id=resource or key,
        risk=risk,
        effort=effort,
    )


def findings(db: Database, org_id) -> dict[str, Finding]:
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        return {f.dedup_key: f for f in s.scalars(select(Finding))}


def test_lifecycle_dedup_and_failures(db: Database, fake_detectors) -> None:
    org_id, _ = make_org(db, "Acme", "owner@acme.com")
    CURRENT["t.volumes"] = [item("vol-1", 40), item("vol-2", 10)]
    CURRENT["t.gp3"] = [item("vol-1", 8)]  # same volume: never counted twice
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        r = service.run(s, org_id)
    assert r["t.volumes"] == {"found": 2, "new": 2, "gone": 0}
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        rows = list(s.scalars(select(Finding)))
        summary = service.summary(rows)
    assert (
        summary["savings_monthly"] == 50.0 and summary["overlapping"] == 1 and summary["count"] == 3
    )
    # vol-2 is fixed; vol-1 dismissed by a person stays dismissed when seen again.
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        f = s.scalars(select(Finding).where(Finding.dedup_key == "t.volumes:vol-1")).one()
        f.status = "dismissed"
    CURRENT["t.volumes"] = [item("vol-1", 45)]
    CURRENT["t.gp3"] = RuntimeError("API throttled")  # a failing detector keeps its findings
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        r = service.run(s, org_id)
    assert r["t.gp3"] == {"error": "RuntimeError"}
    rows = findings(db, org_id)
    assert rows["t.volumes:vol-2"].status == "gone" and rows["t.volumes:vol-2"].resolved_at
    assert (
        rows["t.volumes:vol-1"].status == "dismissed"
        and float(rows["t.volumes:vol-1"].savings_monthly) == 45
    )
    assert rows["t.gp3:vol-1"].status == "open"
    # It comes back.
    CURRENT["t.volumes"] = [item("vol-1", 45), item("vol-2", 10)]
    CURRENT["t.gp3"] = [item("vol-1", 8)]
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        service.run(s, org_id)
    assert findings(db, org_id)["t.volumes:vol-2"].status == "open"


def test_ranking_discounts_risk_and_effort(db: Database, fake_detectors) -> None:
    org_id, _ = make_org(db, "Acme", "owner@acme.com")
    CURRENT["t.volumes"] = [
        item("big-risky", 100, risk="high", effort="high"),
        item("small-easy", 30),
    ]
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        service.run(s, org_id)
        ranked = sorted(s.scalars(select(Finding)), key=service.score, reverse=True)
    assert [f.dedup_key for f in ranked] == [
        "t.volumes:small-easy",
        "t.volumes:big-risky",
    ]  # 30 > 100*.4*.35


def test_api_and_demo(client: TestClient, db: Database) -> None:
    from ccradar.demo.seed import ensure_demo, load_all_seeders

    load_all_seeders()
    with db.session() as s:
        demo_id = ensure_demo(s).id
    _, visitor = make_org(db, "Visitor", "visitor@x.com")
    add_to_org(db, demo_id, "visitor@x.com", Role.VIEWER)
    v = sign_in_client(client, db, visitor, demo_id)
    r = v.get("/api/v1/findings").json()
    assert r["summary"]["count"] >= 1 and r["summary"]["savings_monthly"] > 0
    top = next(i for i in r["items"] if "payments-gateway" in i["title"])
    assert top["detector"] == "k8s.rightsizing" and top["commands"][0].startswith(
        "kubectl -n payments set resources"
    )
    assert {
        "name": "k8s.rightsizing",
        "category": "kubernetes",
        "title": "Over-sized container requests",
    } in r["detectors"]
    assert (
        v.patch(f"/api/v1/findings/{top['id']}", json={"status": "dismissed"}).status_code == 403
    )  # demo read-only
    # Own organisation: members dismiss with a note; it is audited; others cannot see it.
    org_id, owner = make_org(db, "Acme", "owner@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        f = Finding(
            org_id=org_id,
            detector="x",
            category="waste",
            dedup_key="x:1",
            overlap_key="1",
            title="t",
            savings_monthly=12,
            currency="USD",
            risk="low",
            effort="low",
            first_seen_at=utcnow(),
            last_seen_at=utcnow(),
        )
        s.add(f)
        s.flush()
        fid = str(f.id)
    member = add_to_org(db, org_id, "dev@acme.com", Role.MEMBER)
    m = sign_in_client(client, db, member, org_id)
    d = m.patch(
        f"/api/v1/findings/{fid}", json={"status": "dismissed", "note": "kept for DR drills"}
    ).json()
    assert d["status"] == "dismissed" and d["status_note"] == "kept for DR drills"
    assert m.get("/api/v1/findings").json()["summary"]["count"] == 0
    assert m.get("/api/v1/findings", params={"status": "dismissed"}).json()["items"][0]["id"] == fid
    assert m.post("/api/v1/findings/run").status_code == 200
    trail = (
        sign_in_client(client, db, owner, org_id)
        .get("/api/v1/org/audit-events", params={"action": "finding"})
        .json()["items"]
    )
    assert any(e["changes"].get("status") == ["open", "dismissed"] for e in trail)
    other_id, other = make_org(db, "Other", "o@other.com")
    assert (
        sign_in_client(client, db, other, other_id)
        .patch(f"/api/v1/findings/{fid}", json={"status": "open"})
        .status_code
        == 404
    )
