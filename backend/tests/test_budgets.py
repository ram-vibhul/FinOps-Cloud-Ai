from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from costlens.accounts.roles import Role
from costlens.costs import budgets
from costlens.costs.ingest import CostLine, replace_billing_period
from costlens.db import Database
from costlens.db.tenant import set_tenant
from tests.helpers import add_to_org, make_org, sign_in_client

TODAY = date(2026, 11, 20)


def test_period_bounds() -> None:
    assert budgets.period_bounds("monthly", TODAY) == (
        date(2026, 11, 1),
        date(2026, 11, 30),
        "2026-11",
    )
    assert budgets.period_bounds("quarterly", TODAY) == (
        date(2026, 10, 1),
        date(2026, 12, 31),
        "2026-Q4",
    )
    assert budgets.period_bounds("annual", TODAY) == (date(2026, 1, 1), date(2026, 12, 31), "2026")


def _bill(db: Database, org_id) -> None:
    lines = [
        CostLine(
            charge_date=date(2026, 11, d),
            provider="aws",
            service_name=svc,
            sub_account_id=acct,
            billed_cost=Decimal(c),
            effective_cost=Decimal(c),
            billing_currency="USD",
            tags={"team": team},
        )
        for d in range(1, 20)
        for svc, acct, team, c in (
            ("Amazon EC2", "111", "payments", "100"),
            ("Amazon S3", "111", "data", "20"),
            ("Amazon EC2", "222", "payments", "50"),
        )
    ]
    with db.session() as s:
        replace_billing_period(
            s,
            org_id=org_id,
            source_kind="t",
            source_key="t",
            billing_period=date(2026, 11, 1),
            lines=lines,
        )


def test_nested_budgets_status_and_alerts(client: TestClient, db: Database, monkeypatch) -> None:
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    _bill(db, org_id)
    member = add_to_org(db, org_id, "m@acme.com", Role.MEMBER)
    m = sign_in_client(client, db, member, org_id)
    root = m.post(
        "/api/v1/budgets",
        json={
            "name": "Account 111",
            "amount": 4000,
            "filters": {"account": ["111"]},
            "recipients": ["cfo@acme.com"],
        },
    )
    assert root.status_code == 201, root.text
    rid = root.json()["id"]
    child = m.post(
        "/api/v1/budgets",
        json={
            "name": "Payments EC2",
            "parent_id": rid,
            "amount": 2500,
            "filters": {"service": ["Amazon EC2"], "tag:team": ["payments"]},
        },
    )
    cid = child.json()["id"]
    grandchild = m.post(
        "/api/v1/budgets",
        json={
            "name": "Wrong account",
            "parent_id": cid,
            "amount": 10,
            "filters": {"account": ["222"]},
        },
    )
    assert (
        m.put(
            f"/api/v1/budgets/{rid}", json={"name": "loop", "amount": 1, "parent_id": cid}
        ).json()["code"]
        == "budget_cycle"
    )
    assert (
        m.post(
            "/api/v1/budgets", json={"name": "x", "amount": 1, "filters": {"bogus": ["a"]}}
        ).status_code
        == 422
    )
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        rows = {r["name"]: r for r in budgets.all_status(s, org_id, today=TODAY)}
    assert rows["Account 111"]["spent"] == 19 * 120  # EC2 + S3 in account 111
    assert rows["Payments EC2"]["spent"] == 19 * 100  # ANDed with the parent's account filter
    assert rows["Payments EC2"]["effective_filters"] == {
        "account": ["111"],
        "service": ["Amazon EC2"],
        "tag:team": ["payments"],
    }
    assert (
        rows["Wrong account"]["spent"] == 0 and rows["Wrong account"]["effective_filters"] is None
    )
    assert grandchild.status_code == 201
    # Forecast: 1,900 spent + 100/day for the 11 days after the last data day.
    assert rows["Payments EC2"]["forecast"] == pytest.approx(1900 + 100 * 11)
    assert (
        rows["Account 111"]["allocated_to_children"] == 2500
        and not rows["Account 111"]["over_allocated"]
    )
    sent: list = []
    emails: list = []
    monkeypatch.setattr(
        budgets, "notify", lambda db, event, messages: sent.extend(m.title for m in messages)
    )
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        n = budgets.check_alerts(
            s, org_id, today=TODAY, enqueue=lambda name, message: emails.append(message)
        )
        again = budgets.check_alerts(
            s, org_id, today=TODAY, enqueue=lambda name, message: emails.append(message)
        )
    # Payments EC2: 76% spent, forecast 120% -> forecast alerts for 80 and 100.
    # Account 111: 57% spent, forecast 2,280 + 120 x 11 = 3,600 = 90% -> forecast alert for 80.
    assert n == 3 and again == 0
    assert "Payments EC2: on course for 120% of the budget" in sent
    assert [e["to"] for e in emails] == ["cfo@acme.com"] and "on course for 90%" in emails[0][
        "subject"
    ]
    o = sign_in_client(client, db, owner, org_id)
    assert o.delete(f"/api/v1/budgets/{rid}").status_code == 204
    assert o.get("/api/v1/budgets").json() == []  # children went with it
