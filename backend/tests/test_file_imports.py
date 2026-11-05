from __future__ import annotations

import io
import json
from datetime import date
from decimal import Decimal

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from costlens.accounts.roles import Role
from costlens.costs import files
from costlens.costs.parsing import ValueProblem, parse_date, parse_decimal, parse_tags
from costlens.db import Database
from costlens.db.tenant import set_tenant
from costlens.jobs import load_all_jobs
from costlens.models import CostDaily, CostLineItem
from tests.helpers import add_to_org, make_org, sign_in_client

FOCUS_HEADER = (
    "ChargePeriodStart,BillingPeriodStart,ProviderName,BillingAccountId,SubAccountId,"
    "SubAccountName,ServiceName,ServiceCategory,RegionId,ResourceId,ChargeCategory,"
    "PricingCategory,BilledCost,EffectiveCost,ListCost,BillingCurrency,ConsumedQuantity,"
    "ConsumedUnit,Tags"
)


def focus_csv(rows: list[str]) -> bytes:
    return ("\n".join([FOCUS_HEADER, *rows]) + "\n").encode()


FOCUS_ROWS = [
    "2026-10-01T00:00:00Z,2026-10-01,Microsoft,ba1,sub-1,Prod,Virtual Machines,Compute,eastus,"
    'vm-1,Usage,Standard,10.00,8.00,12.00,USD,24,Hours,"{""team"":""data""}"',
    "2026-10-01T01:00:00Z,2026-10-01,Microsoft,ba1,sub-1,Prod,Virtual Machines,Compute,eastus,"
    'vm-1,Usage,Standard,10.00,8.00,12.00,USD,24,Hours,"{""team"":""data""}"',
    "2026-11-02T00:00:00Z,2026-11-01,Microsoft,ba1,sub-1,Prod,Storage,Storage,eastus,,Usage,,5.50,5.50,,USD,,,",
    "2026-11-02T00:00:00Z,2026-11-01,Microsoft,ba1,sub-1,Prod,Tax,,,,Tax,,1.00,1.00,,USD,,,",
]


@pytest.fixture
def env(client: TestClient, db: Database):
    load_all_jobs()
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    viewer = add_to_org(db, org_id, "v@acme.com", Role.VIEWER)
    c = sign_in_client(client, db, owner, org_id)
    return {"c": c, "db": db, "org": org_id, "owner": owner, "viewer": viewer}


def upload(c: TestClient, name: str, data: bytes):
    return c.post(
        "/api/v1/imports/files",
        params={"filename": name},
        content=data,
        headers={"Content-Type": "application/octet-stream"},
    )


def total(db: Database, org_id) -> Decimal:
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        return Decimal(str(s.scalar(select(func.sum(CostDaily.billed_cost))) or 0))


# --- parsers ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1,234.50", Decimal("1234.50")),
        ("$12", Decimal(12)),
        ("(3.25)", Decimal("-3.25")),
        ("₹ 99", Decimal(99)),
        ("", Decimal(0)),
        (7, Decimal(7)),
    ],
)
def test_parse_decimal(raw, expected) -> None:
    assert parse_decimal(raw) == expected


def test_parse_errors() -> None:
    for bad in ("abc", "NaN"):
        with pytest.raises(ValueProblem):
            parse_decimal(bad)
    assert parse_date("05/11/2026", "%d/%m/%Y") == date(2026, 11, 5)
    with pytest.raises(ValueProblem, match="YYYY-MM-DD"):
        parse_date("05/11/2026")
    assert parse_tags('{"a": "1", "b": null}') == {"a": "1"}
    with pytest.raises(ValueProblem):
        parse_tags("[1, 2]")


def test_suggest_mapping() -> None:
    mapping = files.suggest_mapping(["Usage Date", "Service", "Cost (USD)", "Account ID", "Region"])
    assert mapping == {
        "charge_date": "Usage Date",
        "service_name": "Service",
        "cost": "Cost (USD)",
        "sub_account_id": "Account ID",
        "region": "Region",
    }


# --- FOCUS ---------------------------------------------------------------------------


def test_focus_csv_upload_and_import(env) -> None:
    c = env["c"]
    r = upload(c, "azure-focus.csv", focus_csv(FOCUS_ROWS))
    assert r.status_code == 201, r.text
    up = r.json()
    assert up["detected_format"] == "focus" and up["file_type"] == "csv"
    report = c.post(f"/api/v1/imports/files/{up['id']}/validate", json={}).json()
    assert report["rows_valid"] == 4 and report["error_count"] == 0
    assert report["billing_periods"] == {"2026-10": 2, "2026-11": 2}
    done = c.post(f"/api/v1/imports/files/{up['id']}/import", json={}).json()
    listed = c.get("/api/v1/imports/files").json()[0]
    assert listed["status"] == "imported" and listed["rows_imported"] == 4
    assert total(env["db"], env["org"]) == Decimal("26.50")
    with env["db"].session() as s:
        set_tenant(s, org_id=env["org"])
        vm = s.scalar(select(CostLineItem).where(CostLineItem.resource_id == "vm-1"))
        assert vm is not None
        assert (vm.provider, vm.region, vm.tags) == ("microsoft", "eastus", {"team": "data"})
        assert Decimal(str(vm.consumed_quantity)) == Decimal(48)  # two hours -> one day
        assert Decimal(str(vm.effective_cost)) == Decimal(16)
    assert done["status"] in ("queued", "imported")  # in-process jobs may finish first


def test_focus_parquet_upload(env) -> None:
    table = pa.Table.from_pylist(
        [
            {
                "ChargePeriodStart": "2026-11-03T00:00:00Z",
                "BilledCost": 4.25,
                "EffectiveCost": 4.0,
                "BillingCurrency": "EUR",
                "ServiceName": "Compute Engine",
                "ProviderName": "Google Cloud",
                "SubAccountId": "proj-1",
                "ChargeCategory": "Usage",
                "Tags": None,
            }
        ]
    )
    buf = io.BytesIO()
    pq.write_table(table, buf)
    up = upload(env["c"], "gcp.parquet", buf.getvalue()).json()
    assert (up["file_type"], up["detected_format"]) == ("parquet", "focus")
    env["c"].post(f"/api/v1/imports/files/{up['id']}/import", json={})
    with env["db"].session() as s:
        set_tenant(s, org_id=env["org"])
        line = s.scalar(select(CostLineItem))
        assert line is not None and (line.provider, line.billing_currency) == (
            "google_cloud",
            "EUR",
        )


# --- custom CSV ----------------------------------------------------------------------------


CUSTOM = (
    b"Usage Date,Product,Amount,Team,Env\n"
    b'05/11/2026,Datadog,"1,200.00",platform,prod\n'
    b"06/11/2026,Datadog,300,platform,\n"
    b"07/11/2026,Snowflake,(25.00),data,prod\n"
)


def test_custom_csv_with_mapping(env) -> None:
    c = env["c"]
    up = upload(c, "saas.csv", CUSTOM).json()
    assert up["detected_format"] == "custom"
    preview = c.get(f"/api/v1/imports/files/{up['id']}/preview").json()
    assert preview["columns"] == ["Usage Date", "Product", "Amount", "Team", "Env"]
    assert preview["sample"][0]["Product"] == "Datadog"
    settings = {
        "mapping": {
            "charge_date": "Usage Date",
            "service_name": "Product",
            "cost": "Amount",
            "_tag_columns": ["Team", "Env"],
        },
        "provider": "saas",
        "currency": "USD",
        "date_format": "%d/%m/%Y",
    }
    report = c.post(f"/api/v1/imports/files/{up['id']}/validate", json=settings).json()
    assert report["error_count"] == 0 and report["totals"] == {"USD": "1475.00"}
    c.post(f"/api/v1/imports/files/{up['id']}/import", json=settings)
    with env["db"].session() as s:
        set_tenant(s, org_id=env["org"])
        rows = s.execute(
            select(
                CostLineItem.service_name, CostLineItem.charge_category, CostLineItem.tags
            ).order_by(CostLineItem.charge_date)
        ).all()
    assert rows[0] == ("Datadog", "Usage", {"Team": "platform", "Env": "prod"})
    assert rows[2][1] == "Credit"  # negative amount in parentheses


def test_mapping_checks(env) -> None:
    c = env["c"]
    up = upload(c, "saas.csv", CUSTOM).json()
    r = c.post(
        f"/api/v1/imports/files/{up['id']}/validate",
        json={"mapping": {"charge_date": "Usage Date", "cost": "Amount"}},
    )
    assert r.status_code == 400 and "service_name" in r.json()["detail"]
    r = c.post(
        f"/api/v1/imports/files/{up['id']}/validate",
        json={
            "mapping": {"charge_date": "Usage Date", "cost": "Amount", "service_name": "Product"}
        },
    )
    assert "currency" in r.json()["detail"]
    r = c.post(
        f"/api/v1/imports/files/{up['id']}/validate",
        json={
            "mapping": {"charge_date": "Nope", "cost": "Amount", "service_name": "Product"},
            "currency": "USD",
        },
    )
    assert r.json()["code"] == "unknown_columns"


def test_errors_reported_and_threshold(env) -> None:
    c = env["c"]
    data = focus_csv(
        [
            *FOCUS_ROWS,
            "not-a-date,2026-11-01,Microsoft,ba1,s,P,Storage,,,,Usage,,1,1,,USD,,,",
            "2026-11-03,2026-11-01,Microsoft,ba1,s,P,Storage,,,,Usage,,abc,1,,USD,,,",
            "2026-11-03,2026-11-01,Microsoft,ba1,s,P,Storage,,,,Usage,,1,1,,dollars,,,",
        ]
    )
    up = upload(c, "bad.csv", data).json()
    report = c.post(f"/api/v1/imports/files/{up['id']}/validate", json={}).json()
    assert report["error_count"] == 3
    assert [e["row"] for e in report["errors"]] == [6, 7, 8]
    assert "not a date" in report["errors"][0]["message"]
    c.post(f"/api/v1/imports/files/{up['id']}/import", json={})
    refused = c.get("/api/v1/imports/files").json()[0]
    assert refused["status"] == "failed" and refused["report"]["refused"] is True
    assert total(env["db"], env["org"]) == 0
    c.post(f"/api/v1/imports/files/{up['id']}/import", json={"allow_partial": True})
    assert total(env["db"], env["org"]) == Decimal("26.50")


def test_reimport_replaces_and_delete_removes(env) -> None:
    c = env["c"]
    up = upload(c, "f.csv", focus_csv(FOCUS_ROWS)).json()
    for _ in range(2):
        c.post(f"/api/v1/imports/files/{up['id']}/import", json={})
    assert total(env["db"], env["org"]) == Decimal("26.50")
    assert c.delete(f"/api/v1/imports/files/{up['id']}").status_code == 204
    assert total(env["db"], env["org"]) == 0
    assert c.get("/api/v1/imports/files").json() == []


def test_bad_uploads_and_permissions(env) -> None:
    c = env["c"]
    assert upload(c, "x.csv", b"").json()["code"] == "empty_file"
    assert upload(c, "x.parquet", b"PAR1garbage").json()["code"] == "unreadable_file"
    v = sign_in_client(c, env["db"], env["viewer"], env["org"])
    assert upload(v, "f.csv", focus_csv(FOCUS_ROWS)).status_code == 403
    assert v.get("/api/v1/imports/files").status_code == 200


def test_focus_from_aws_s3(db: Database) -> None:
    from costlens.clouds.aws import cur
    from costlens.models import CloudConnection
    from tests.fakes.aws import FakeAws, FakeS3

    org_id, _ = make_org(db, "Acme", "o@acme.com")
    role = "arn:aws:iam::111111111111:role/CostLensReadOnly"
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        conn = CloudConnection(
            org_id=org_id,
            provider="aws",
            name="P",
            status="active",
            external_id="e",
            role_arn=role,
            account_id="111111111111",
            cur_bucket="b",
            cur_prefix="focus",
            settings={"export_format": "focus"},
        )
        s.add(conn)
        s.flush()
        conn_id = conn.id
    table = pa.Table.from_pylist(
        [
            {
                "ChargePeriodStart": f"2026-11-03T0{h}:00:00Z",
                "BilledCost": 1.5,
                "EffectiveCost": 1.0,
                "BillingCurrency": "USD",
                "ServiceName": "Amazon EC2",
                "ProviderName": "AWS",
                "SubAccountId": "222222222222",
                "ChargeCategory": "Usage",
                "Tags": json.dumps({"team": "x"}),
            }
            for h in range(4)
        ]
    )
    buf = io.BytesIO()
    pq.write_table(table, buf)
    s3 = FakeS3()
    s3.put("b", "focus/exp/data/BILLING_PERIOD=2026-11/part-0.parquet", buf.getvalue())
    aws = FakeAws(clients={"s3": s3})
    aws.allow(role, "e")
    with db.session() as s:
        [run] = cur.import_connection(s, aws, conn_id, months=1, today=date(2026, 11, 5))
        assert run.source_kind == "aws_focus" and run.rows == 1
    assert total(db, org_id) == Decimal("6.0")
