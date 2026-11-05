"""Import the AWS Cost and Usage Report 2.0 (Data Exports, Parquet) from S3.

Layout written by AWS Data Exports::

    s3://<bucket>/<prefix>/<export name>/data/BILLING_PERIOD=YYYY-MM/*.parquet

For each billing period we list the Parquet files, skip the period if their ETags
have not changed since the last import, otherwise stream every file in record
batches (pyarrow), aggregate hourly rows to one line per day and key, and replace
the period (idempotent). Savings Plans and Reserved Instances are amortised so
``effective_cost`` matches AWS's "amortized cost" view.
"""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pyarrow.parquet as pq
from sqlalchemy.orm import Session

from costlens.clouds.aws.client import AwsAccessError, AwsGateway
from costlens.costs.ingest import CostLine, month_start, replace_billing_period
from costlens.db.base import utcnow
from costlens.db.tenant import set_tenant
from costlens.models import CloudConnection, CostImportRun

log = logging.getLogger(__name__)
ZERO = Decimal(0)
BATCH_ROWS = 50_000
HOURLY_DAYS = 14

# CUR 2.0 columns we read (absent columns are treated as empty).
COLUMNS = [
    "line_item_usage_start_date",
    "line_item_line_item_type",
    "bill_payer_account_id",
    "line_item_usage_account_id",
    "line_item_usage_account_name",
    "line_item_product_code",
    "product",
    "product_region_code",
    "line_item_availability_zone",
    "line_item_resource_id",
    "line_item_usage_type",
    "line_item_operation",
    "line_item_usage_amount",
    "pricing_unit",
    "line_item_currency_code",
    "line_item_unblended_cost",
    "pricing_public_on_demand_cost",
    "savings_plan_savings_plan_effective_cost",
    "savings_plan_total_commitment_to_date",
    "savings_plan_used_commitment",
    "reservation_effective_cost",
    "reservation_unused_amortized_upfront_fee_for_billing_period",
    "reservation_unused_recurring_fee",
    "savings_plan_savings_plan_a_r_n",
    "reservation_reservation_a_r_n",
    "resource_tags",
]

CREDIT_TYPES = {
    "Credit",
    "Refund",
    "BundledDiscount",
    "EdpDiscount",
    "PrivateRateDiscount",
    "SppDiscount",
    "DistributorDiscount",
}
FEE_TYPES = {"Fee", "RIFee", "SavingsPlanRecurringFee", "SavingsPlanUpfrontFee"}
COMMITTED_TYPES = {
    "SavingsPlanCoveredUsage",
    "SavingsPlanNegation",
    "DiscountedUsage",
    "RIFee",
    "SavingsPlanRecurringFee",
    "SavingsPlanUpfrontFee",
}


def _dec(value: Any) -> Decimal:
    if value is None or value == "":
        return ZERO
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _map(value: Any) -> dict[str, str]:
    """CUR 2.0 map columns arrive as dicts or lists of (key, value) pairs."""
    if not value:
        return {}
    items = value.items() if isinstance(value, dict) else value
    return {str(k): str(v) for k, v in items if v not in (None, "")}


def normalise_tags(raw: dict[str, str]) -> dict[str, str]:
    """``user_team`` -> ``team``; AWS system tags keep their ``aws_`` prefix."""
    return {(k.removeprefix("user_") if k.startswith("user_") else k): v for k, v in raw.items()}


def amortise(line_type: str, row: dict[str, Any]) -> tuple[Decimal, Decimal]:  # noqa: PLR0911
    """(billed, effective) for one CUR row."""
    billed = _dec(row.get("line_item_unblended_cost"))
    if line_type == "SavingsPlanCoveredUsage":
        return billed, _dec(row.get("savings_plan_savings_plan_effective_cost"))
    if line_type == "SavingsPlanNegation":
        return billed, ZERO  # the covered usage already carries the effective cost
    if line_type == "SavingsPlanRecurringFee":
        unused = _dec(row.get("savings_plan_total_commitment_to_date")) - _dec(
            row.get("savings_plan_used_commitment")
        )
        return billed, max(unused, ZERO)
    if line_type == "SavingsPlanUpfrontFee":
        return billed, ZERO  # spread over the term via the covered usage
    if line_type == "DiscountedUsage":
        return billed, _dec(row.get("reservation_effective_cost"))
    if line_type == "RIFee":
        return billed, (
            _dec(row.get("reservation_unused_amortized_upfront_fee_for_billing_period"))
            + _dec(row.get("reservation_unused_recurring_fee"))
        )
    return billed, billed


def categorise(line_type: str, usage_type: str) -> tuple[str, str | None]:
    if line_type == "Tax":
        return "Tax", None
    if line_type in CREDIT_TYPES:
        return "Credit", None
    if line_type in FEE_TYPES:
        return "Purchase", "Committed"
    if line_type in COMMITTED_TYPES:
        return "Usage", "Committed"
    if "Spot" in usage_type:
        return "Usage", "Dynamic"
    return "Usage", "Standard"


@dataclass
class _Acc:
    line: CostLine
    hours: int = 0


def _day(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()


def aggregate(
    batches: Iterator[list[dict[str, Any]]],
    hourly: dict[tuple[Any, ...], Decimal] | None = None,
    hourly_since: datetime | None = None,
) -> list[CostLine]:
    """Sum hourly rows into one line per day and key."""
    acc: dict[tuple[Any, ...], _Acc] = {}
    for rows in batches:
        for row in rows:
            line_type = str(row.get("line_item_line_item_type") or "Usage")
            usage_type = str(row.get("line_item_usage_type") or "")
            billed, effective = amortise(line_type, row)
            quantity = _dec(row.get("line_item_usage_amount"))
            if billed == 0 and effective == 0 and quantity == 0:
                continue
            product = _map(row.get("product"))
            tags = normalise_tags(_map(row.get("resource_tags")))
            charge, pricing = categorise(line_type, usage_type)
            service = product.get("product_name") or str(row.get("line_item_product_code") or "")
            key = (
                _day(row.get("line_item_usage_start_date")),
                row.get("line_item_usage_account_id"),
                service,
                row.get("product_region_code"),
                row.get("line_item_availability_zone"),
                row.get("line_item_resource_id"),
                usage_type,
                row.get("line_item_operation"),
                line_type,
                tuple(sorted(tags.items())),
                row.get("line_item_currency_code"),
            )
            entry = acc.get(key)
            if entry is None:
                commitment = (
                    row.get("savings_plan_savings_plan_a_r_n")
                    or row.get("reservation_reservation_a_r_n")
                    or None
                )
                entry = acc[key] = _Acc(
                    CostLine(
                        charge_date=key[0],
                        provider="aws",
                        billing_account_id=row.get("bill_payer_account_id"),
                        sub_account_id=key[1],
                        sub_account_name=row.get("line_item_usage_account_name"),
                        service_name=service or "Unknown",
                        service_category=product.get("product_family"),
                        region=key[3] or None,
                        availability_zone=key[4] or None,
                        resource_id=key[5] or None,
                        usage_type=usage_type or None,
                        operation=key[7] or None,
                        charge_category=charge,
                        pricing_category=pricing,
                        commitment_discount_id=commitment,
                        commitment_discount_type=(
                            "Savings Plan"
                            if "savingsplan" in str(commitment)
                            else "Reservation"
                            if commitment
                            else None
                        ),
                        consumed_quantity=ZERO,
                        consumed_unit=row.get("pricing_unit"),
                        billed_cost=ZERO,
                        effective_cost=ZERO,
                        list_cost=ZERO,
                        billing_currency=str(row.get("line_item_currency_code") or "USD"),
                        tags=tags,
                        extras={"line_item_type": line_type},
                    )
                )
            line = entry.line
            line.billed_cost += billed
            line.effective_cost += effective
            line.list_cost = (line.list_cost or ZERO) + _dec(
                row.get("pricing_public_on_demand_cost")
            )
            line.consumed_quantity = (line.consumed_quantity or ZERO) + quantity
            entry.hours += 1
            if hourly is not None:
                start_at = row.get("line_item_usage_start_date")
                if isinstance(start_at, datetime):
                    hour = start_at.replace(
                        minute=0, second=0, microsecond=0, tzinfo=start_at.tzinfo or UTC
                    )
                    if hourly_since is None or hour >= hourly_since:
                        hkey = (hour, key[1], key[2], key[10])
                        hourly[hkey] = hourly.get(hkey, ZERO) + effective
    return [e.line for e in acc.values()]


def read_parquet(path: str, *, all_columns: bool = False) -> Iterator[list[dict[str, Any]]]:
    parquet = pq.ParquetFile(path)
    present = None if all_columns else [c for c in COLUMNS if c in parquet.schema_arrow.names]
    for batch in parquet.iter_batches(batch_size=BATCH_ROWS, columns=present):
        yield batch.to_pylist()


# --- S3 -----------------------------------------------------------------------


def list_period_files(s3: Any, bucket: str, prefix: str, period: date) -> list[dict[str, Any]]:
    marker = f"BILLING_PERIOD={period:%Y-%m}/"
    found: list[dict[str, Any]] = []
    token = None
    while True:
        args: dict[str, Any] = {
            "Bucket": bucket,
            "Prefix": prefix.strip("/") + "/" if prefix else "",
        }
        if token:
            args["ContinuationToken"] = token
        page = s3.list_objects_v2(**args)
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if marker in key and key.endswith(".parquet") and "/data/" in f"/{key}":
                found.append(obj)
        if not page.get("IsTruncated"):
            return sorted(found, key=lambda o: o["Key"])
        token = page.get("NextContinuationToken")


def _stream_files(
    s3: Any, bucket: str, files: list[dict[str, Any]], *, all_columns: bool = False
) -> Iterator[list[dict[str, Any]]]:
    for obj in files:
        with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
            s3.download_fileobj(bucket, obj["Key"], tmp)
            tmp.flush()
            yield from read_parquet(tmp.name, all_columns=all_columns)


def _focus_lines(batches: Iterator[list[dict[str, Any]]]) -> list[CostLine]:
    from costlens.costs import focus

    return focus.aggregate_daily(
        focus.row_to_line(row, default_provider="aws") for rows in batches for row in rows
    )


def store_hourly(
    db: Session, org_id: Any, source_key: str, period: date, hourly: dict[tuple[Any, ...], Decimal]
) -> int:
    """Replace this source's hourly rows for the billing period."""
    import uuid as _uuid

    from sqlalchemy import delete, insert

    from costlens.db.base import Base

    table = Base.metadata.tables["cost_hourly"]
    start = datetime.combine(period, datetime.min.time(), UTC)
    end = datetime.combine((period + timedelta(days=32)).replace(day=1), datetime.min.time(), UTC)
    set_tenant(db, org_id=org_id)
    db.execute(
        delete(table).where(
            table.c.org_id == org_id,
            table.c.source_key == source_key,
            table.c.hour >= start,
            table.c.hour < end,
        )
    )
    rows = [
        {
            "id": _uuid.uuid4(),
            "org_id": org_id,
            "hour": h,
            "source_key": source_key,
            "sub_account_id": account,
            "service_name": service or "Unknown",
            "billing_currency": currency or "USD",
            "effective_cost": value,
        }
        for (h, account, service, currency), value in hourly.items()
        if value
    ]
    if rows:
        db.execute(insert(table), rows)
    return len(rows)


def recent_periods(today: date, months: int) -> list[date]:
    periods, current = [], month_start(today)
    for _ in range(months):
        periods.append(current)
        current = (current - timedelta(days=1)).replace(day=1)
    return list(reversed(periods))


def import_connection(
    db: Session,
    gateway: AwsGateway,
    connection_id: Any,
    *,
    months: int = 3,
    today: date | None = None,
    force: bool = False,
) -> list[CostImportRun]:
    set_tenant(db, bypass=True)
    conn = db.get(CloudConnection, connection_id)
    if conn is None or conn.status != "active" or not conn.role_arn or not conn.cur_bucket:
        return []
    org_id, bucket, prefix = conn.org_id, conn.cur_bucket, conn.cur_prefix or ""
    try:
        session = gateway.assume(conn.role_arn, conn.external_id, f"costlens-{org_id}")
        s3 = gateway.client(session, "s3")
    except AwsAccessError as exc:
        conn.status, conn.last_error = "error", str(exc)
        db.flush()
        return []
    seen: dict[str, list[str]] = dict((conn.settings or {}).get("cur_etags", {}))
    export_format = (conn.settings or {}).get("export_format", "cur2")
    runs = []
    for period in recent_periods(today or utcnow().date(), months):
        try:
            files = list_period_files(s3, bucket, prefix, period)
        except Exception as exc:  # botocore ClientError: bucket missing, access denied
            set_tenant(db, bypass=True)
            conn.last_error = f"Could not list s3://{bucket}/{prefix}: {type(exc).__name__}"
            db.flush()
            return runs
        etags = sorted(f"{o['Key']}:{o.get('ETag', '')}" for o in files)
        if not files or (not force and seen.get(period.isoformat()) == etags):
            continue
        if export_format == "focus":  # AWS Data Exports in FOCUS 1.0 format
            lines = _focus_lines(_stream_files(s3, bucket, files, all_columns=True))
        else:
            hourly: dict[tuple[Any, ...], Decimal] = {}
            since = datetime.combine(
                utcnow().date() - timedelta(days=HOURLY_DAYS), datetime.min.time(), UTC
            )
            lines = aggregate(_stream_files(s3, bucket, files), hourly, since)
            store_hourly(db, org_id, f"aws_cur:{conn.id}", period, hourly)
        runs.append(
            replace_billing_period(
                db,
                org_id=org_id,
                source_kind="aws_focus" if export_format == "focus" else "aws_cur",
                source_key=f"aws_cur:{conn.id}",
                billing_period=period,
                lines=lines,
            )
        )
        seen[period.isoformat()] = etags
    set_tenant(db, bypass=True)
    conn.settings = {**(conn.settings or {}), "cur_etags": seen}
    conn.last_checked_at, conn.last_error = utcnow(), None
    db.flush()
    return runs
