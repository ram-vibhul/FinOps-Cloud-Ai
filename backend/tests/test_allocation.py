from __future__ import annotations

import random
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from costlens.accounts.roles import Role
from costlens.costs.allocation import split_cents
from costlens.costs.ingest import CostLine, replace_billing_period
from costlens.db import Database
from tests.helpers import add_to_org, make_org, sign_in_client


def test_split_cents_always_adds_up() -> None:
    assert split_cents(Decimal("100.00"), {"a": 1, "b": 1, "c": 1}) == {
        "a": Decimal("33.34"),
        "b": Decimal("33.33"),
        "c": Decimal("33.33"),
    }
    rng = random.Random(7)  # noqa: S311 - test data, not secrets
    for _ in range(500):  # invariant: whole cents, exact total, never negative for positive weights
        total = Decimal(rng.randint(0, 10_000_000)) / 100
        weights = {
            f"k{i}": rng.random() * rng.choice([1, 100, 10_000]) for i in range(rng.randint(1, 12))
        }
        parts = split_cents(total, weights)
        assert sum(parts.values()) == total
        assert all(p >= 0 and p == p.quantize(Decimal("0.01")) for p in parts.values())
    assert split_cents(Decimal("10"), {"a": 0, "b": 0}) == {
        "a": Decimal("5.00"),
        "b": Decimal("5.00"),
    }


def test_showback_and_rules(client: TestClient, db: Database) -> None:
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    month = date(2026, 10, 1)
    lines = [
        CostLine(
            charge_date=date(2026, 10, d),
            provider="aws",
            service_name=svc,
            billed_cost=Decimal(c),
            effective_cost=Decimal(c),
            billing_currency="USD",
            tags=tags,
        )
        for d in range(1, 11)
        for svc, c, tags in (
            ("Amazon EC2", "30", {"team": "payments"}),
            ("Amazon EC2", "10", {"team": "search"}),
            ("Amazon S3", "5", {}),  # untagged
            ("AWS Support", "7.01", {}),  # shared: split by direct cost
            ("Amazon CloudWatch", "3", {"team": "platform"}),  # shared: fixed 50/50
        )
    ]
    with db.session() as s:
        replace_billing_period(
            s, org_id=org_id, source_kind="t", source_key="t", billing_period=month, lines=lines
        )
    member = add_to_org(db, org_id, "m@acme.com", Role.MEMBER)
    m = sign_in_client(client, db, member, org_id)
    rule = {
        "name": "Support",
        "dimension": "tag:team",
        "filters": {"service": ["AWS Support"]},
        "method": "proportional",
        "targets": ["payments", "search"],
    }
    assert m.post("/api/v1/allocation/rules", json=rule).status_code == 403
    o = sign_in_client(client, db, owner, org_id)
    assert o.post("/api/v1/allocation/rules", json=rule).status_code == 201
    bad = {
        "name": "Platform",
        "dimension": "tag:team",
        "filters": {"tag:team": ["platform"]},
        "method": "fixed",
        "percents": {"payments": 50, "search": 40},
    }
    assert o.post("/api/v1/allocation/rules", json=bad).json()["code"] == "percents_not_100"
    assert (
        o.post(
            "/api/v1/allocation/rules", json={**bad, "percents": {"payments": 50, "search": 50}}
        ).status_code
        == 201
    )
    data = o.get(
        "/api/v1/allocation/showback", params={"dimension": "tag:team", "month": "2026-10"}
    ).json()
    rows = {r["value"]: r for r in data["rows"]}
    assert rows["payments"]["direct"] == 300 and rows["search"]["direct"] == 100
    # Support 70.10 split 3:1 -> 52.58 / 17.52 (to the cent); platform 30 split 50/50.
    assert rows["payments"]["shared"] == {"Support": 52.58, "Platform": 15.0}
    assert rows["search"]["shared"] == {"Support": 17.52, "Platform": 15.0}
    assert (
        rows["(untagged)"]["total"] == 50 and "platform" not in rows
    )  # the pool went to its targets
    assert data["allocated_total"] == pytest.approx(data["total"]) == pytest.approx(550.1)
    assert data["adds_up"] is True
    csv_text = o.get(
        "/api/v1/allocation/showback.csv", params={"dimension": "tag:team", "month": "2026-10"}
    ).text
    assert "tag:team,direct,shared: Platform,shared: Support,total,currency" in csv_text
    assert (
        "payments,300.00,15.00,52.58,367.58,USD" in csv_text and "TOTAL,,,,550.10,USD" in csv_text
    )
