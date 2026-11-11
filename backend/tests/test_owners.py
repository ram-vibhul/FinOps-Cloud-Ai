from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select

from ccradar.accounts.roles import Role
from ccradar.costs.ingest import CostLine, replace_billing_period
from ccradar.db import Database
from ccradar.db.base import utcnow
from ccradar.db.tenant import set_tenant
from ccradar.findings.registry import FindingIn
from ccradar.findings.service import _store
from ccradar.inventory.aws import Found
from ccradar.inventory.service import upsert
from ccradar.models import Finding
from tests.helpers import add_to_org, make_org, sign_in_client


def _seed(db: Database):
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        upsert(
            s,
            org_id,
            "111",
            [
                Found(
                    "EC2",
                    "ebs_volume",
                    "vol-pay",
                    "r",
                    state="available",
                    tags={"Team": "Payments"},
                )
            ],
        )
        _store(
            s,
            org_id,
            "aws.ebs_unattached",
            "waste",
            [
                FindingIn(
                    key=rid,
                    title=rid,
                    savings_monthly=10,
                    currency="USD",
                    provider="aws",
                    account_id=acct,
                    resource_id=rid,
                )
                for rid, acct in (("vol-pay", "111"), ("vol-data", "444"), ("vol-x", "999"))
            ],
        )
        _store(
            s,
            org_id,
            "k8s.rightsizing",
            "kubernetes",
            [
                FindingIn(
                    key="c/search/Deployment/api/app",
                    title="k",
                    savings_monthly=5,
                    currency="USD",
                    provider="kubernetes",
                    resource_id="c/search/Deployment/api",
                )
            ],
        )
    line = CostLine(
        charge_date=utcnow().date() - timedelta(days=2),
        provider="aws",
        service_name="EC2",
        resource_id="vol-x",
        billed_cost=Decimal(1),
        effective_cost=Decimal(1),
        billing_currency="USD",
    )
    with db.session() as s:
        replace_billing_period(
            s,
            org_id=org_id,
            source_kind="t",
            source_key="t",
            billing_period=line.charge_date.replace(day=1),
            lines=[line],
        )
        from ccradar.models import CostLineItem

        set_tenant(s, org_id=org_id)
        for row in s.scalars(select(CostLineItem).where(CostLineItem.resource_id == "vol-x")):
            row.vtags = {"Product": "Checkout"}
    return org_id, owner


def _owned(db: Database, org_id) -> dict[str, tuple]:
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        return {
            f.resource_id or "": (f.team, f.owner_id, f.owner_source)
            for f in s.scalars(select(Finding))
        }


def test_routing_rules(client: TestClient, db: Database) -> None:
    org_id, owner = _seed(db)
    member = add_to_org(db, org_id, "m@acme.com", Role.MEMBER)
    m = sign_in_client(client, db, member, org_id)
    rule = {
        "name": "Payments",
        "kind": "tag",
        "key": "team",
        "value": "payments",
        "team": "payments",
    }
    assert m.post("/api/v1/findings/owner-rules", json=rule).status_code == 403
    o = sign_in_client(client, db, owner, org_id)
    assert (
        o.post("/api/v1/findings/owner-rules", json={**rule, "key": ""}).json()["code"]
        == "key_required"
    )
    assert (
        o.post("/api/v1/findings/owner-rules", json={**rule, "team": None}).json()["code"]
        == "owner_required"
    )
    r = o.post("/api/v1/findings/owner-rules", json={**rule, "owner_id": str(member)})
    assert r.status_code == 201 and r.json()["findings"] == 1
    o.post(
        "/api/v1/findings/owner-rules",
        json={
            "name": "Data account",
            "priority": 50,
            "kind": "account",
            "value": "444",
            "team": "data",
        },
    )
    o.post(
        "/api/v1/findings/owner-rules",
        json={"name": "Search ns", "kind": "namespace", "value": "search", "team": "search"},
    )
    o.post(
        "/api/v1/findings/owner-rules",
        json={
            "name": "Checkout product",
            "kind": "vtag",
            "key": "product",
            "value": "checkout",
            "team": "checkout",
        },
    )
    # A catch-all that loses to everything more specific (higher number = lower priority).
    o.post(
        "/api/v1/findings/owner-rules",
        json={
            "name": "Everything AWS",
            "priority": 900,
            "kind": "service",
            "value": "aws",
            "team": "platform",
        },
    )
    owned = _owned(db, org_id)
    assert owned["vol-pay"] == ("payments", member, "rule")
    assert owned["vol-data"][0] == "data" and owned["c/search/Deployment/api"][0] == "search"
    assert owned["vol-x"][0] == "checkout"  # virtual tag from the bill
    mine = o.get("/api/v1/findings", params={"team": "payments"}).json()["items"]
    assert [f["resource_id"] for f in mine] == ["vol-pay"] and mine[0]["owner_source"] == "rule"
    m = sign_in_client(client, db, member, org_id)
    assert [
        f["resource_id"] for f in m.get("/api/v1/findings", params={"owner": "me"}).json()["items"]
    ] == ["vol-pay"]
    # A person's choice wins and survives rule changes.
    data_id = next(
        f["id"] for f in m.get("/api/v1/findings").json()["items"] if f["resource_id"] == "vol-data"
    )
    m.put(f"/api/v1/findings/{data_id}/owner", json={"owner_id": str(member)})
    o = sign_in_client(client, db, owner, org_id)
    rules = {x["name"]: x["id"] for x in o.get("/api/v1/findings/owner-rules").json()}
    o.delete(f"/api/v1/findings/owner-rules/{rules['Data account']}")
    owned = _owned(db, org_id)
    assert owned["vol-data"][1] == member and owned["vol-data"][2] == "person"
    o.delete(f"/api/v1/findings/owner-rules/{rules['Checkout product']}")
    assert _owned(db, org_id)["vol-x"][0] == "platform"  # falls through to the catch-all
