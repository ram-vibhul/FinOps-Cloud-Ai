from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from ccradar.costs.ingest import CostLine, replace_billing_period
from ccradar.db import Database
from ccradar.db.tenant import set_tenant
from ccradar.models import Organization, User
from tests.helpers import make_org, sign_in_client


def make_partner(db: Database) -> tuple[uuid.UUID, uuid.UUID]:
    pid, staff = make_org(db, "Cloud Partners Ltd", "staff@partner.com")
    with db.session() as s:
        set_tenant(s, bypass=True)
        org = s.get(Organization, pid)
        assert org is not None
        org.is_partner = True
    return pid, staff


def bill(db: Database, org_id: uuid.UUID) -> None:
    with db.session() as s:
        replace_billing_period(
            s,
            org_id=org_id,
            source_kind="t",
            source_key="t",
            billing_period=date(2026, 10, 1),
            lines=[
                CostLine(
                    charge_date=date(2026, 10, 5),
                    provider="aws",
                    service_name=svc,
                    billed_cost=Decimal(v),
                    effective_cost=Decimal(v),
                    billing_currency="USD",
                )
                for svc, v in (("Amazon EC2", "100.00"), ("Amazon S3", "33.33"))
            ],
        )


def test_partner_creates_client_switches_in_and_rebills(client: TestClient, db: Database) -> None:
    pid, staff = make_partner(db)
    c = sign_in_client(client, db, staff, pid)
    new = c.post("/api/v1/partner/clients", json={"name": "Acme Retail", "markup_pct": 10}).json()
    cid = new["id"]
    bill(db, uuid.UUID(cid))
    # The client shows up in the organisation switcher, and staff can enter it.
    me = c.get("/api/v1/auth/me").json()
    assert {o["name"]: o["role"] for o in me["organizations"]} == {
        "Cloud Partners Ltd": "owner",
        "Acme Retail": "owner",
    }
    assert c.post("/api/v1/auth/switch", json={"organization_id": cid}).status_code == 204
    assert c.get("/api/v1/auth/me").json()["active_organization_id"] == cid
    assert c.put("/api/v1/org/alert-settings", json={"min_impact": 5}).status_code == 200
    c.post("/api/v1/auth/switch", json={"organization_id": str(pid)})
    r = c.get(f"/api/v1/partner/clients/{cid}/rebill", params={"month": "2026-10"}).json()
    assert r["cost"] == 133.33 and r["markup_pct"] == 10.0
    assert r["lines"][0] == {"service": "Amazon EC2", "cost": 100.0, "rebilled": 110.0}
    assert r["rebilled"] == 146.66 and r["margin"] == 13.33  # per line, rounded to the cent
    clients = c.get("/api/v1/partner/clients").json()
    assert clients[0]["last_month_rebilled"] in (146.66, 0.0)  # depends on today's month
    assert (
        c.patch(f"/api/v1/partner/clients/{cid}", json={"markup_pct": 0}).json()["markup_pct"]
        == 0.0
    )
    # Branding is shown to the client.
    assert (
        c.put(
            "/api/v1/partner/branding",
            json={"display_name": "Cloud Partners", "primary_color": "nope"},
        ).json()["code"]
        == "bad_color"
    )
    c.put(
        "/api/v1/partner/branding",
        json={"display_name": "Cloud Partners", "primary_color": "#0f766e"},
    )
    c.post("/api/v1/auth/switch", json={"organization_id": cid})
    assert c.get("/api/v1/org/branding").json() == {
        "display_name": "Cloud Partners",
        "primary_color": "#0f766e",
    }


def test_link_code_consent_and_leaving(client: TestClient, db: Database) -> None:
    pid, staff = make_partner(db)
    oid, owner = make_org(db, "Indie Co", "o@indie.com")
    _, stranger = make_org(db, "Other", "x@other.com")
    p = sign_in_client(client, db, staff, pid)
    code = p.post("/api/v1/partner/link-codes").json()["code"]
    assert code.startswith("clp_")
    # Staff cannot see the organisation before its owner agrees.
    assert p.post("/api/v1/auth/switch", json={"organization_id": str(oid)}).status_code == 404
    o = sign_in_client(client, db, owner, oid)
    assert (
        o.post("/api/v1/org/partner", json={"code": "clp_wrong-code"}).json()["code"] == "bad_code"
    )
    assert o.post("/api/v1/org/partner", json={"code": code}).status_code == 204
    p = sign_in_client(client, db, staff, pid)
    roles = {x["name"]: x["role"] for x in p.get("/api/v1/auth/me").json()["organizations"]}
    assert roles["Indie Co"] == "admin"  # linked organisations give admin, not owner
    # Non-partner organisations cannot use partner endpoints; strangers see nothing.
    s = sign_in_client(client, db, stranger)
    assert s.get("/api/v1/partner/clients").json()["code"] == "not_partner"
    # The client leaves: access ends.
    o = sign_in_client(client, db, owner, oid)
    assert o.delete("/api/v1/org/partner").status_code == 204
    p = sign_in_client(client, db, staff, pid)
    assert p.post("/api/v1/auth/switch", json={"organization_id": str(oid)}).status_code == 404


def test_only_platform_staff_mark_partners(client: TestClient, db: Database) -> None:
    oid, owner = make_org(db, "Acme", "o@acme.com")
    c = sign_in_client(client, db, owner, oid)
    assert (
        c.put(
            f"/api/v1/platform/organizations/{oid}/partner", json={"is_partner": True}
        ).status_code
        == 403
    )
    with db.session() as s:
        set_tenant(s, bypass=True)
        u = s.get(User, owner)
        assert u is not None
        u.is_platform_admin = True
    assert (
        c.put(f"/api/v1/platform/organizations/{oid}/partner", json={"is_partner": True}).json()[
            "is_partner"
        ]
        is True
    )


def test_portfolio_order_and_branded_statement(
    client: TestClient, db: Database, monkeypatch
) -> None:
    from ccradar.models import CloudConnection
    from ccradar.reports import rebill

    pid, staff = make_partner(db)
    c = sign_in_client(client, db, staff, pid)
    calm = c.post("/api/v1/partner/clients", json={"name": "Calm Co", "markup_pct": 10}).json()[
        "id"
    ]
    busy = c.post("/api/v1/partner/clients", json={"name": "Busy Co", "markup_pct": 10}).json()[
        "id"
    ]
    bill(db, uuid.UUID(calm))
    with db.session() as s:
        set_tenant(s, org_id=uuid.UUID(busy))
        s.add(
            CloudConnection(
                org_id=uuid.UUID(busy),
                provider="aws",
                name="Prod",
                status="error",
                external_id="x1",
                last_error="AccessDenied",
            )
        )
    rows = c.get("/api/v1/partner/portfolio").json()
    assert rows[0]["name"] == "Busy Co"
    assert rows[0]["reasons"] == ["1 connection(s) failing"] and rows[0]["attention"] >= 50
    c.put(
        "/api/v1/partner/branding",
        json={"display_name": "Cloud Partners", "primary_color": "#0f766e"},
    )
    seen: dict = {}
    real = rebill.to_pdf

    def capture(*args, **kw):
        seen["args"] = args
        return real(*args, compress=False)

    monkeypatch.setattr(rebill, "to_pdf", capture)
    r = c.get(f"/api/v1/partner/clients/{calm}/statement.pdf", params={"month": "2026-10"})
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    name, color, client_name, month, currency, lines, total = seen["args"]
    assert (name, color, client_name, month, currency) == (
        "Cloud Partners",
        "#0f766e",
        "Calm Co",
        "October 2026",
        "USD",
    )
    assert (
        lines == [("Amazon EC2", 110.0), ("Amazon S3", 36.66)] and total == 146.66
    )  # re-billed only
    text = r.content
    assert b"Cloud Partners" in text and b"Calm Co" in text and b"146.66" in text
    assert (
        b"133.33" not in text and b"13.33" not in text and b"100.00" not in text
    )  # never cost or margin
