from __future__ import annotations

from datetime import UTC, date, datetime

from fastapi.testclient import TestClient
from sqlalchemy import select

from ccradar.accounts.roles import Role
from ccradar.db import Database
from ccradar.db.tenant import set_tenant
from ccradar.findings.registry import FindingIn
from ccradar.findings.service import _store
from ccradar.models import Finding, PlannedPurchase
from tests.helpers import add_to_org, make_org, sign_in_client


def test_roi_pack(client: TestClient, db: Database) -> None:
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        _store(
            s,
            org_id,
            "aws.ec2_rightsizing",
            "rightsizing",
            [
                FindingIn(
                    key=k, title=t, savings_monthly=v, currency="USD", provider="aws", resource_id=k
                )
                for k, t, v in (
                    ("i-1", "Downsize i-1", 200),
                    ("i-2", "Downsize i-2", 300),
                    ("i-3", "Downsize i-3", 50),
                )
            ],
        )
        rows = {f.resource_id: f for f in s.scalars(select(Finding))}
        held = rows["i-1"]
        held.status, held.verified_at, held.realised_monthly = (
            "verified",
            datetime(2026, 8, 20, tzinfo=UTC),
            200,
        )
        held.measurement = {
            "before_daily": 10,
            "after_daily": 3.4,
            "history": [{"month": "2026-09", "kept_monthly": 180}],
        }
        back = rows["i-2"]  # came back after one good month
        back.status, back.realised_monthly = "open", 300
        back.measurement = {
            "regressed_at": "2026-10-03T07:50:00",
            "history": [
                {"month": "2026-09", "kept_monthly": 290},
                {"month": "2026-10", "kept_monthly": 20},
            ],
        }
        rows["i-3"].status = "applied"
        s.add(
            PlannedPurchase(
                org_id=org_id,
                kind="savings_plan",
                description="Compute SP",
                term="1yr",
                payment="no_upfront",
                monthly_cost=700,
                currency="USD",
                planned_for=date(2026, 8, 1),
                status="purchased",
                commitment_id="sp-1",
                purchased_on=date(2026, 8, 1),
                verification=[
                    {"month": "2026-09", "saving": 250.0},
                    {"month": "2026-10", "saving": 260.0},
                ],
            )
        )
    member = add_to_org(db, org_id, "m@acme.com", Role.MEMBER)
    m = sign_in_client(client, db, member, org_id)
    assert (
        m.put("/api/v1/roi/settings", json={"monthly_price": 100, "currency": "USD"}).status_code
        == 403
    )
    o = sign_in_client(client, db, owner, org_id)
    assert (
        o.put("/api/v1/roi/settings", json={"monthly_price": 100, "currency": "USD"}).status_code
        == 200
    )
    params = {"start": "2026-09-01", "end": "2026-10-31"}
    data = o.get("/api/v1/roi", params=params).json()
    totals = {r["title"]: r["total"] for r in data["verified"]}
    # i-1: September rechecked (180) + October confirmed (200); i-2: only its rechecked months.
    assert totals == {"Downsize i-1": 380.0, "Downsize i-2": 310.0, "Compute SP": 510.0}
    assert (
        data["verified_total"] == 1200.0
        and data["product_cost"] == 200.0
        and data["roi_multiple"] == 6.0
    )
    assert data["estimated_monthly"] == 50.0 and data["estimated"][0]["title"] == "Downsize i-3"
    assert data["net"] == 1000.0
    csv_text = o.get("/api/v1/roi/pack.csv", params=params).text
    assert (
        "Verified savings,1200.0,USD" in csv_text and "estimated,finding,Downsize i-3" in csv_text
    )
    pdf = o.get("/api/v1/roi/pack.pdf", params=params)
    assert pdf.headers["content-type"] == "application/pdf" and pdf.content.startswith(b"%PDF")
    assert pdf.headers["content-disposition"].endswith('ccradar-roi-2026-09-01-2026-10-31.pdf"')


def test_month_span() -> None:
    from ccradar.reports.roi import _months

    assert _months(date(2025, 11, 5), date(2026, 2, 1)) == [
        "2025-11",
        "2025-12",
        "2026-01",
        "2026-02",
    ]
