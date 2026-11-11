from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from ccradar.clouds.aws import cost_explorer
from ccradar.config import Settings
from ccradar.db import Database
from ccradar.db.tenant import set_tenant
from ccradar.jobs import InProcessRunner, load_all_jobs
from ccradar.main import create_app
from ccradar.models import CloudAccount, CloudConnection, CostDaily, CostLineItem
from tests.conftest import offline_http_client, public_resolver
from tests.fakes.aws import FakeAws, FakeCostExplorer
from tests.helpers import make_org, sign_in_client

ROLE = "arn:aws:iam::111111111111:role/CloudCostRadarReadOnly"
ROWS = [
    ("2026-10-30", "111111111111", "Amazon Elastic Compute Cloud - Compute", "100.00", "80.00"),
    ("2026-10-30", "222222222222", "Amazon Simple Storage Service", "10.50", "10.50"),
    ("2026-10-31", "111111111111", "Tax", "18.00", "18.00"),
    ("2026-10-31", "222222222222", "Amazon Simple Storage Service", "0", "0"),
    ("2026-11-01", "111111111111", "Savings Plans for AWS Compute usage", "500.00", "0"),
    ("2026-11-01", "222222222222", "Amazon Simple Storage Service", "-5.00", "-5.00"),
    ("2026-11-02", "222222222222", "Amazon Simple Storage Service", "11.00", "11.00"),
    ("2026-11-03", "222222222222", "Amazon Simple Storage Service", "11.25", "11.25"),
    ("2026-11-04", "222222222222", "Amazon Simple Storage Service", "11.75", "11.75"),
]
TODAY = date(2026, 11, 4)


@pytest.fixture
def env(settings: Settings, db: Database):
    load_all_jobs()
    settings.aws_principal_arn = "arn:aws:iam::999999999999:role/collector"
    ce = FakeCostExplorer(
        rows=list(ROWS), names={"111111111111": "Payer", "222222222222": "Data Lake"}
    )
    aws = FakeAws(clients={"ce": ce})
    app = create_app(
        settings,
        db=db,
        runner=InProcessRunner(),
        resolver=public_resolver,
        http_client=offline_http_client(),
        aws=aws,
    )
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        conn = CloudConnection(
            org_id=org_id,
            provider="aws",
            name="Payer",
            status="active",
            external_id="ccradar-ext",
            role_arn=ROLE,
            account_id="111111111111",
        )
        s.add(conn)
        s.flush()
        conn_id = conn.id
    aws.allow(ROLE, "ccradar-ext")
    with TestClient(app) as c:
        yield {
            "c": c,
            "db": db,
            "aws": aws,
            "ce": ce,
            "org": org_id,
            "owner": owner,
            "conn": conn_id,
        }


def totals(db: Database, org_id) -> dict[str, Decimal]:
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        rows = s.execute(
            select(CostDaily.charge_category, func.sum(CostDaily.billed_cost)).group_by(
                CostDaily.charge_category
            )
        ).all()
    return {k: Decimal(str(v)).quantize(Decimal("0.01")) for k, v in rows}


def test_periods() -> None:
    assert cost_explorer.periods(date(2026, 11, 4), 2) == [
        (date(2026, 10, 1), date(2026, 11, 1)),
        (date(2026, 11, 1), date(2026, 11, 5)),
    ]
    assert cost_explorer.periods(date(2026, 1, 15), 2)[0] == (date(2025, 12, 1), date(2026, 1, 1))


@pytest.mark.parametrize(
    ("service", "amount", "expected"),
    [
        ("Tax", "10", ("Tax", None)),
        ("Amazon S3", "-1", ("Credit", None)),
        ("Savings Plans for AWS Compute usage", "5", ("Purchase", "Committed")),
        ("Reserved Instance", "5", ("Purchase", "Committed")),
        ("AWS Lambda", "5", ("Usage", "Standard")),
    ],
)
def test_classify(service: str, amount: str, expected: tuple) -> None:
    assert cost_explorer.classify(service, Decimal(amount)) == expected


def test_import_two_months(env) -> None:
    with env["db"].session() as s:
        runs = cost_explorer.import_connection(s, env["aws"], env["conn"], months=2, today=TODAY)
    assert [(r.billing_period, r.rows) for r in runs] == [
        (date(2026, 10, 1), 3),
        (date(2026, 11, 1), 5),
    ]  # zero-cost row skipped
    assert totals(env["db"], env["org"]) == {
        "Usage": Decimal("144.50"),
        "Tax": Decimal("18.00"),
        "Purchase": Decimal("500.00"),
        "Credit": Decimal("-5.00"),
    }
    requests = env["ce"].requests
    assert requests[0]["Granularity"] == "DAILY"
    assert requests[0]["TimePeriod"] == {"Start": "2026-10-01", "End": "2026-11-01"}
    assert any("NextPageToken" in r for r in requests)  # pagination followed
    with env["db"].session() as s:
        set_tenant(s, org_id=env["org"])
        names = dict(s.execute(select(CloudAccount.external_id, CloudAccount.name)).all())
        estimated = s.scalars(
            select(CostLineItem.extras).where(CostLineItem.charge_date == date(2026, 11, 4))
        ).all()
    assert names == {"111111111111": "Payer", "222222222222": "Data Lake"}
    assert estimated == [{"estimated": True}]


def test_reimport_is_idempotent(env) -> None:
    for _ in range(2):
        with env["db"].session() as s:
            cost_explorer.import_connection(s, env["aws"], env["conn"], months=2, today=TODAY)
    assert totals(env["db"], env["org"])["Usage"] == Decimal("144.50")


def test_amortized_is_effective_cost(env) -> None:
    with env["db"].session() as s:
        cost_explorer.import_connection(s, env["aws"], env["conn"], months=2, today=TODAY)
    with env["db"].session() as s:
        set_tenant(s, org_id=env["org"])
        ec2 = s.execute(
            select(CostLineItem.billed_cost, CostLineItem.effective_cost).where(
                CostLineItem.service_name.like("%Compute Cloud%")
            )
        ).one()
    assert (Decimal(str(ec2[0])), Decimal(str(ec2[1]))) == (Decimal("100"), Decimal("80"))


def test_revoked_role_marks_connection(env) -> None:
    env["aws"].trusted.clear()
    with env["db"].session() as s:
        assert cost_explorer.import_connection(s, env["aws"], env["conn"], today=TODAY) == []
    with env["db"].session() as s:
        set_tenant(s, org_id=env["org"])
        conn = s.get(CloudConnection, env["conn"])
        assert conn is not None and conn.status == "error"


def test_collect_now_and_history(env) -> None:
    c = sign_in_client(env["c"], env["db"], env["owner"], env["org"])
    r = c.post(f"/api/v1/connections/{env['conn']}/import", json={"months": 1})
    assert r.status_code == 202
    history = c.get(f"/api/v1/connections/{env['conn']}/imports").json()
    assert history[0]["status"] == "succeeded" and history[0]["source_kind"] == "aws_ce"
    assert (
        c.post(f"/api/v1/connections/{env['conn']}/import", json={"months": 99}).status_code == 422
    )


def test_scheduler_picks_the_source(env) -> None:
    from ccradar.jobs.costs import schedule_imports

    assert schedule_imports() == 1
    assert env["ce"].requests  # Cost Explorer used without a CUR bucket
    env["ce"].requests.clear()
    with env["db"].session() as s:
        set_tenant(s, org_id=env["org"])
        conn = s.get(CloudConnection, env["conn"])
        assert conn is not None
        conn.cur_bucket = "acme-cur"
    from tests.fakes.aws import FakeS3

    env["aws"].clients["s3"] = FakeS3()
    assert schedule_imports() == 1
    assert env["ce"].requests == []  # CUR configured: Cost Explorer not called
