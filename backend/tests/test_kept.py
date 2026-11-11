from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ccradar.costs.ingest import CostLine, replace_billing_period
from ccradar.db import Database
from ccradar.db.base import utcnow
from ccradar.db.tenant import set_tenant
from ccradar.findings import kept
from ccradar.findings.registry import FindingIn
from ccradar.findings.service import _store
from ccradar.models import Finding, FindingEvent
from tests.helpers import make_org, sign_in_client


def test_recheck_keeps_and_catches_regressions(
    client: TestClient, db: Database, monkeypatch
) -> None:
    sent: list = []
    monkeypatch.setattr(
        kept, "notify", lambda db, event, messages: sent.extend((event, m.title) for m in messages)
    )
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    today = utcnow().date()
    last_month_end = today.replace(day=1) - timedelta(days=1)
    start = last_month_end.replace(day=1)
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        _store(
            s,
            org_id,
            "aws.ec2_rightsizing",
            "rightsizing",
            [
                FindingIn(
                    key=f"size:{rid}",
                    title=f"Downsize {rid}",
                    savings_monthly=200,
                    currency="USD",
                    provider="aws",
                    resource_id=rid,
                )
                for rid in ("i-kept", "i-back")
            ],
        )
        for f in s.scalars(select(Finding)):
            f.status = "verified"
            f.verified_at = datetime.combine(start - timedelta(days=5), datetime.min.time(), UTC)
            f.realised_monthly = 200
            f.measurement = {"before_daily": 10.0, "currency": "USD"}
    days = (last_month_end - start).days + 1
    lines = [
        CostLine(
            charge_date=start + timedelta(days=d),
            provider="aws",
            service_name="EC2",
            resource_id=rid,
            billed_cost=Decimal(cost),
            effective_cost=Decimal(cost),
            billing_currency="USD",
        )
        for d in range(days)
        for rid, cost in (("i-kept", "3"), ("i-back", "9"))
    ]
    with db.session() as s:
        replace_billing_period(
            s, org_id=org_id, source_kind="t", source_key="t", billing_period=start, lines=lines
        )
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        assert kept.recheck(s, today) == {"checked": 2, "regressed": 1}
        assert kept.recheck(s, today) == {"checked": 0, "regressed": 0}  # once a month
        rows = {f.resource_id: f for f in s.scalars(select(Finding))}
        assert rows["i-kept"].status == "verified"
        assert rows["i-kept"].measurement["history"][0]["kept_monthly"] == pytest.approx(
            7 * 30.4, abs=0.01
        )
        back = rows["i-back"]
        assert back.status == "open" and "came back" in (back.status_note or "")
        events = s.scalars(select(FindingEvent).where(FindingEvent.finding_id == back.id)).all()
        assert any((e.data or {}).get("regressed") for e in events)
    assert sent == [("saving_regressed", "Saving came back: Downsize i-back")]
    c = sign_in_client(client, db, owner, org_id)
    summary = c.get("/api/v1/findings/kept").json()
    label = start.strftime("%Y-%m")
    assert summary["latest_month"] == label
    assert summary["kept_latest_month"] == pytest.approx(7 * 30.4 + 1 * 30.4, abs=0.01)
    assert [r["title"] for r in summary["regressions"]] == ["Downsize i-back"]
