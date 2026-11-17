"""Investigate a cost change (11.32)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from ccradar.costs.ingest import CostLine, replace_billing_period
from ccradar.costs.investigate import investigate
from ccradar.db import Database
from ccradar.db.tenant import set_tenant
from ccradar.models import ChangeEvent, Finding
from tests.helpers import make_org, sign_in_client

END = date(2026, 11, 14)


def line(day: date, service: str, cost: float, resource: str, account: str = "1") -> CostLine:
    return CostLine(
        charge_date=day,
        provider="aws",
        service_name=service,
        sub_account_id=account,
        region="ap-south-1",
        resource_id=resource,
        billed_cost=Decimal(str(cost)),
        effective_cost=Decimal(str(cost)),
        billing_currency="USD",
    )


class Writer:
    def __init__(self) -> None:
        self.facts: list[str] = []

    def rewrite(self, facts: str, system: str | None = None) -> str:
        self.facts.append(facts)
        return "Plain words."


def test_finds_the_driver_its_change_and_a_fix(client: TestClient, db: Database) -> None:
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    lines = []
    for i in range(14):
        day = END - timedelta(days=13 - i)
        late = i >= 7  # the last 7 days: the new server
        lines += [line(day, "EC2", 10, "i-old"), line(day, "S3", 5, "bucket")]
        if late:
            lines.append(line(day, "EC2", 40, "i-big", account="2"))
    with db.session() as s:
        replace_billing_period(
            s,
            org_id=org_id,
            source_kind="t",
            source_key="t",
            billing_period=date(2026, 11, 1),
            lines=lines,
        )
        set_tenant(s, org_id=org_id)
        s.add(
            ChangeEvent(
                org_id=org_id,
                source="cloudtrail",
                kind="infrastructure",
                title="RunInstances i-big",
                external_id="e1",
                actor="ravi",
                occurred_at=datetime(2026, 11, 8, 9, tzinfo=UTC),
                resource_id="i-big",
                service="EC2",
                attributes={"impact": {"monthly": 1216.0}},
            )
        )
        s.add(
            Finding(
                org_id=org_id,
                detector="aws.ec2_rightsizing",
                category="rightsizing",
                dedup_key=f"x:{uuid.uuid4().hex}",
                overlap_key="i-big",
                title="Right-size i-big",
                savings_monthly=600,
                currency="USD",
                risk="low",
                effort="low",
                first_seen_at=datetime.now(UTC),
                last_seen_at=datetime.now(UTC),
                provider="aws",
                resource_id="i-big",
                service="EC2",
            )
        )
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        writer = Writer()
        out = investigate(s, org_id, END - timedelta(days=6), END, writer)
    assert (out["now"], out["before"], out["change"]) == (385.0, 105.0, 280.0)
    assert out["top_service"] == "EC2" and out["drivers"][0]["change"] == 280.0
    assert out["within"]["resource"][0]["label"].startswith("i-big")
    assert out["within"]["account"][0]["change"] == 280.0
    assert (
        out["changes"][0]["title"] == "RunInstances i-big"
        and out["changes"][0]["monthly"] == 1216.0
    )
    assert out["fixes"][0]["title"] == "Right-size i-big"
    assert out["summary"] == "Plain words." and out["worded_by_ai"] is True
    assert "+280.00" in writer.facts[0] and "ravi" in writer.facts[0]

    api = sign_in_client(client, db, owner, org_id).post(
        "/api/v1/costs/investigate", json={"days": 7}
    )
    body = api.json()
    assert api.status_code == 200 and body["worded_by_ai"] is False  # no AI on this server
    assert body["summary"].startswith("Spend 2026-11-08 to 2026-11-14")
