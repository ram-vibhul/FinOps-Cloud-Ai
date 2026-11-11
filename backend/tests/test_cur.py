from __future__ import annotations

import io
from datetime import date, datetime
from decimal import Decimal

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from sqlalchemy import func, select

from ccradar.clouds.aws import cur
from ccradar.db import Database
from ccradar.db.tenant import set_tenant
from ccradar.models import CloudConnection, CostDaily, CostLineItem
from tests.fakes.aws import FakeAws, FakeS3
from tests.helpers import make_org

ROLE = "arn:aws:iam::111111111111:role/CloudCostRadarReadOnly"
BUCKET, PREFIX = "acme-billing", "exports"


def cur_row(
    hour: int,
    day: int = 3,
    *,
    kind: str = "Usage",
    cost: str = "1.00",
    service: str = "Amazon Elastic Compute Cloud",
    resource: str | None = "i-1",
    tags: dict | None = None,
    **extra,
) -> dict:
    row = {
        "line_item_usage_start_date": datetime(2026, 11, day, hour),
        "line_item_line_item_type": kind,
        "bill_payer_account_id": "111111111111",
        "line_item_usage_account_id": "222222222222",
        "line_item_usage_account_name": "Production",
        "line_item_product_code": "AmazonEC2",
        "product": {"product_name": service, "product_family": "Compute Instance"},
        "product_region_code": "ap-south-1",
        "line_item_availability_zone": "ap-south-1a",
        "line_item_resource_id": resource,
        "line_item_usage_type": "APS3-BoxUsage:m6i.large",
        "line_item_operation": "RunInstances",
        "line_item_usage_amount": "1",
        "pricing_unit": "Hrs",
        "line_item_currency_code": "USD",
        "line_item_unblended_cost": cost,
        "pricing_public_on_demand_cost": cost,
        "savings_plan_savings_plan_effective_cost": None,
        "savings_plan_total_commitment_to_date": None,
        "savings_plan_used_commitment": None,
        "reservation_effective_cost": None,
        "savings_plan_savings_plan_a_r_n": None,
        "resource_tags": tags if tags is not None else {"user_team": "payments"},
    }
    row.update(extra)
    return row


SCHEMA = pa.schema(
    [
        ("line_item_usage_start_date", pa.timestamp("ms")),
        ("line_item_line_item_type", pa.string()),
        ("bill_payer_account_id", pa.string()),
        ("line_item_usage_account_id", pa.string()),
        ("line_item_usage_account_name", pa.string()),
        ("line_item_product_code", pa.string()),
        ("product", pa.map_(pa.string(), pa.string())),
        ("product_region_code", pa.string()),
        ("line_item_availability_zone", pa.string()),
        ("line_item_resource_id", pa.string()),
        ("line_item_usage_type", pa.string()),
        ("line_item_operation", pa.string()),
        ("line_item_usage_amount", pa.decimal128(18, 8)),
        ("pricing_unit", pa.string()),
        ("line_item_currency_code", pa.string()),
        ("line_item_unblended_cost", pa.decimal128(18, 8)),
        ("pricing_public_on_demand_cost", pa.decimal128(18, 8)),
        ("savings_plan_savings_plan_effective_cost", pa.decimal128(18, 8)),
        ("savings_plan_total_commitment_to_date", pa.decimal128(18, 8)),
        ("savings_plan_used_commitment", pa.decimal128(18, 8)),
        ("reservation_effective_cost", pa.decimal128(18, 8)),
        ("savings_plan_savings_plan_a_r_n", pa.string()),
        ("resource_tags", pa.map_(pa.string(), pa.string())),
    ]
)


def parquet(rows: list[dict]) -> bytes:
    def conv(row: dict) -> dict:
        out = dict(row)
        for k in ("product", "resource_tags"):
            out[k] = list((out[k] or {}).items())
        for k, v in out.items():
            if isinstance(v, str) and SCHEMA.field(k).type == pa.decimal128(18, 8):
                out[k] = Decimal(v)
        return out

    table = pa.Table.from_pylist([conv(r) for r in rows], schema=SCHEMA)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


def key(period: str, name: str) -> str:
    return f"{PREFIX}/ccradar/data/BILLING_PERIOD={period}/{name}.parquet"


@pytest.fixture
def env(db: Database):
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        conn = CloudConnection(
            org_id=org_id,
            provider="aws",
            name="Payer",
            status="active",
            external_id="ext",
            role_arn=ROLE,
            account_id="111111111111",
            cur_bucket=BUCKET,
            cur_prefix=PREFIX,
        )
        s.add(conn)
        s.flush()
        conn_id = conn.id
    s3 = FakeS3()
    aws = FakeAws(clients={"s3": s3})
    aws.allow(ROLE, "ext")
    return {"db": db, "org": org_id, "conn": conn_id, "s3": s3, "aws": aws}


def run(env, **kw):
    with env["db"].session() as s:
        return cur.import_connection(s, env["aws"], env["conn"], today=date(2026, 11, 5), **kw)


def lines(env) -> list[CostLineItem]:
    with env["db"].session() as s:
        set_tenant(s, org_id=env["org"])
        return list(
            s.scalars(
                select(CostLineItem).order_by(CostLineItem.charge_date, CostLineItem.service_name)
            )
        )


def test_hourly_rows_become_daily_lines(env) -> None:
    rows = [cur_row(h) for h in range(24)] + [cur_row(h, day=4) for h in range(10)]
    env["s3"].put(BUCKET, key("2026-11", "part-0"), parquet(rows[:20]))
    env["s3"].put(BUCKET, key("2026-11", "part-1"), parquet(rows[20:]))
    env["s3"].put(BUCKET, f"{PREFIX}/ccradar/metadata/BILLING_PERIOD=2026-11/manifest.json", b"{}")
    [run_] = run(env)
    assert run_.rows == 2
    first, second = lines(env)
    assert (first.charge_date, Decimal(str(first.billed_cost))) == (date(2026, 11, 3), Decimal(24))
    assert Decimal(str(second.consumed_quantity)) == Decimal(10)
    assert first.region == "ap-south-1" and first.resource_id == "i-1"
    assert first.tags == {"team": "payments"} and first.sub_account_name == "Production"
    assert "manifest.json" not in " ".join(env["s3"].downloads)


def test_savings_plan_and_ri_amortisation(env) -> None:
    sp = "arn:aws:savingsplans::111111111111:savingsplan/abc"
    rows = [
        cur_row(
            1,
            kind="SavingsPlanCoveredUsage",
            cost="1.00",
            savings_plan_savings_plan_effective_cost="0.70",
            savings_plan_savings_plan_a_r_n=sp,
        ),
        cur_row(1, kind="SavingsPlanNegation", cost="-1.00", savings_plan_savings_plan_a_r_n=sp),
        cur_row(
            1,
            kind="SavingsPlanRecurringFee",
            cost="0.80",
            resource=sp,
            savings_plan_total_commitment_to_date="0.80",
            savings_plan_used_commitment="0.70",
            service="Savings Plans for AWS Compute usage",
        ),
        cur_row(2, kind="Tax", cost="0.30", resource=None, service="Tax"),
        cur_row(3, kind="Credit", cost="-0.50", resource=None),
        cur_row(
            4, kind="Usage", cost="0.20", line_item_usage_type="SpotUsage:m5.large", resource="i-2"
        ),
    ]
    env["s3"].put(BUCKET, key("2026-11", "part-0"), parquet(rows))
    run(env)
    by_type = {li.extras["line_item_type"]: li for li in lines(env)}

    def money(v):
        return Decimal(str(v)).quantize(Decimal("0.01"))

    covered = by_type["SavingsPlanCoveredUsage"]
    assert (money(covered.billed_cost), money(covered.effective_cost)) == (
        Decimal("1.00"),
        Decimal("0.70"),
    )
    assert (
        covered.pricing_category == "Committed"
        and covered.commitment_discount_type == "Savings Plan"
    )
    assert money(by_type["SavingsPlanNegation"].effective_cost) == 0
    fee = by_type["SavingsPlanRecurringFee"]
    assert fee.charge_category == "Purchase" and money(fee.effective_cost) == Decimal("0.10")
    assert by_type["Tax"].charge_category == "Tax"
    assert by_type["Credit"].charge_category == "Credit"
    assert by_type["Usage"].pricing_category == "Dynamic"
    with env["db"].session() as s:
        set_tenant(s, org_id=env["org"])
        effective = s.scalar(select(func.sum(CostDaily.effective_cost)))
        billed = s.scalar(select(func.sum(CostDaily.billed_cost)))
    # Amortised view: SP usage at 0.70 + unused 0.10 + tax + credit + spot.
    assert money(effective) == Decimal("0.80")
    assert money(billed) == Decimal("0.80")


def test_unchanged_files_skipped_changed_reimported(env) -> None:
    env["s3"].put(BUCKET, key("2026-11", "part-0"), parquet([cur_row(1, cost="5")]), etag="a")
    assert len(run(env)) == 1
    assert run(env) == []  # same ETags
    env["s3"].put(BUCKET, key("2026-11", "part-0"), parquet([cur_row(1, cost="7")]), etag="b")
    assert len(run(env)) == 1
    assert [Decimal(str(li.billed_cost)) for li in lines(env)] == [Decimal(7)]
    assert len(run(env, force=True)) == 1  # "collect now" forces


def test_previous_months_and_other_prefixes(env) -> None:
    env["s3"].put(BUCKET, key("2026-10", "part-0"), parquet([cur_row(1, day=15)]))
    env["s3"].put(
        BUCKET, "other/ccradar/data/BILLING_PERIOD=2026-11/x.parquet", parquet([cur_row(1)])
    )
    runs = run(env)
    assert [r.billing_period for r in runs] == [date(2026, 10, 1)]


def test_missing_bucket_records_error(env) -> None:
    class Broken:
        def list_objects_v2(self, **kwargs: object) -> dict:
            raise RuntimeError("NoSuchBucket")

    env["aws"].clients["s3"] = Broken()
    assert run(env) == []
    with env["db"].session() as s:
        set_tenant(s, org_id=env["org"])
        conn = s.get(CloudConnection, env["conn"])
        assert conn is not None and "Could not list" in (conn.last_error or "")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"user_team": "a", "aws_created_by": "x"}, {"team": "a", "aws_created_by": "x"}),
        ({}, {}),
    ],
)
def test_normalise_tags(raw: dict, expected: dict) -> None:
    assert cur.normalise_tags(raw) == expected


def test_recent_periods() -> None:
    assert cur.recent_periods(date(2026, 1, 10), 3) == [
        date(2025, 11, 1),
        date(2025, 12, 1),
        date(2026, 1, 1),
    ]
