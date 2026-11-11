"""Billing files: FOCUS (CSV/Parquet, detected automatically) or any CSV with a mapping.

Flow: upload -> preview (columns, sample rows, suggested mapping) -> validate (dry
run on the first rows) -> import (background job: every row checked; rows with
problems are listed with row number and column; the import is refused if more than
1% of rows fail unless the user accepts partial imports) -> one replace per billing
period, source ``file:<import id>`` (re-importing the same upload replaces it).
"""

from __future__ import annotations

import csv
import io
import tempfile
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

import pyarrow.parquet as pq
from sqlalchemy.orm import Session

from ccradar.core.errors import AppError
from ccradar.core.storage import Storage
from ccradar.costs import focus
from ccradar.costs.ingest import CostDataError, CostLine, month_start, replace_billing_period
from ccradar.costs.parsing import ValueProblem, parse_date, parse_decimal
from ccradar.db.base import utcnow
from ccradar.db.tenant import set_tenant
from ccradar.models import FileImport

MAX_UPLOAD_BYTES = 500 * 1024 * 1024
MAX_ERRORS_LISTED = 25
FAILURE_THRESHOLD = 0.01
DRY_RUN_ROWS = 10_000

# Canonical fields a custom CSV can map to.
CUSTOM_FIELDS = {
    "charge_date": "Date of the cost (required)",
    "cost": "Amount billed (required)",
    "effective_cost": "Amount after discounts (optional; defaults to cost)",
    "service_name": "Service or product (required)",
    "currency": "Currency code column (or set a default currency)",
    "sub_account_id": "Account / subscription / project id",
    "sub_account_name": "Account name",
    "region": "Region",
    "resource_id": "Resource id",
    "resource_name": "Resource name",
    "usage_type": "Usage type / SKU description",
    "quantity": "Usage quantity",
    "unit": "Usage unit",
    "charge_category": "Usage / Purchase / Tax / Credit / Adjustment",
}
SYNONYMS = {
    "charge_date": [
        "date",
        "usage date",
        "usagedate",
        "day",
        "charge date",
        "billing date",
        "usage_start_date",
        "start date",
    ],
    "cost": [
        "cost",
        "amount",
        "billed cost",
        "total",
        "spend",
        "unblended cost",
        "costinbillingcurrency",
        "pretaxcost",
        "cost (usd)",
    ],
    "effective_cost": ["effective cost", "amortized cost", "net cost"],
    "service_name": [
        "service",
        "service name",
        "product",
        "metercategory",
        "product name",
        "servicename",
    ],
    "currency": ["currency", "currency code", "billingcurrency", "billing currency"],
    "sub_account_id": [
        "account",
        "account id",
        "subscription id",
        "project id",
        "subscriptionid",
        "linked account",
    ],
    "sub_account_name": ["account name", "subscription name", "project name"],
    "region": ["region", "location", "resourcelocation"],
    "resource_id": ["resource id", "resourceid", "instance id", "resource"],
    "resource_name": ["resource name"],
    "usage_type": ["usage type", "metername", "sku", "usagetype"],
    "quantity": ["quantity", "usage quantity", "usage amount", "consumedquantity"],
    "unit": ["unit", "unit of measure", "unitofmeasure"],
    "charge_category": ["charge category", "charge type", "chargetype", "record type"],
}


def _norm(name: str) -> str:
    return " ".join(name.strip().lower().replace("_", " ").split())


def suggest_mapping(columns: list[str]) -> dict[str, str]:
    by_norm = {_norm(c): c for c in columns}
    found: dict[str, str] = {}
    for target, options in SYNONYMS.items():
        for option in options:
            column = by_norm.get(_norm(option))
            if column and column not in found.values():
                found[target] = column
                break
    return found


# --- reading -------------------------------------------------------------------


def sniff_type(filename: str, head: bytes) -> str:
    if head[:4] == b"PAR1" or filename.lower().endswith(".parquet"):
        return "parquet"
    return "csv"


def iter_rows(storage: Storage, key: str, file_type: str) -> Iterator[dict[str, Any]]:
    data = storage.get(key)
    if file_type == "parquet":
        with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
            tmp.write(data)
            tmp.flush()
            for batch in pq.ParquetFile(tmp.name).iter_batches(batch_size=50_000):
                yield from batch.to_pylist()
        return
    text = data.decode("utf-8-sig", errors="replace")
    yield from csv.DictReader(io.StringIO(text))


def read_columns(
    storage: Storage, key: str, file_type: str
) -> tuple[list[str], list[dict[str, Any]]]:
    if file_type == "parquet":
        with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
            tmp.write(storage.get(key))
            tmp.flush()
            parquet = pq.ParquetFile(tmp.name)
            columns = list(parquet.schema_arrow.names)
            sample = next(parquet.iter_batches(batch_size=5), None)
            rows = sample.to_pylist() if sample is not None else []
        return columns, rows
    rows_iter = iter_rows(storage, key, file_type)
    sample = [row for _, row in zip(range(5), rows_iter, strict=False)]
    reader = csv.reader(io.StringIO(storage.get(key)[:65536].decode("utf-8-sig", errors="replace")))
    columns = next(reader, [])
    return columns, sample


# --- conversion ---------------------------------------------------------------------


@dataclass
class Options:
    provider: str = "other"
    currency: str | None = None
    date_format: str | None = None


def custom_row_to_line(row: dict[str, Any], mapping: dict[str, str], opts: Options) -> CostLine:
    def col(name: str) -> Any:
        column = mapping.get(name)
        return row.get(column) if column else None

    cost = parse_decimal(col("cost"))
    effective = col("effective_cost")
    currency = str(col("currency") or opts.currency or "").strip().upper()
    service = str(col("service_name") or "").strip()
    charge = str(col("charge_category") or "").strip().capitalize()
    if charge not in ("Usage", "Purchase", "Tax", "Credit", "Adjustment"):
        charge = "Credit" if cost < 0 else "Usage"  # not mapped or unknown: infer from sign
    quantity = col("quantity")
    tags = {
        c.removeprefix("tag:"): str(row[c])
        for c in mapping.get("_tag_columns", [])  # type: ignore[union-attr]
        if row.get(c) not in (None, "")
    }
    return CostLine(
        charge_date=parse_date(col("charge_date"), opts.date_format),
        provider=opts.provider,
        service_name=service[:200],
        billed_cost=cost,
        effective_cost=parse_decimal(effective) if effective not in (None, "") else cost,
        billing_currency=currency,
        charge_category=charge,
        pricing_category=None if charge in ("Tax", "Credit") else "Standard",
        sub_account_id=_s(col("sub_account_id")),
        sub_account_name=_s(col("sub_account_name")),
        region=_s(col("region")),
        resource_id=_s(col("resource_id")),
        resource_name=_s(col("resource_name")),
        usage_type=_s(col("usage_type")),
        consumed_quantity=parse_decimal(quantity) if quantity not in (None, "") else None,
        consumed_unit=_s(col("unit")),
        tags=tags,
    )


def _s(value: Any) -> str | None:
    return None if value in (None, "") else str(value).strip()


@dataclass
class Report:
    rows_read: int = 0
    rows_valid: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    error_count: int = 0
    totals: dict[str, Decimal] = field(default_factory=lambda: defaultdict(Decimal))
    first_date: date | None = None
    last_date: date | None = None
    periods: Counter[str] = field(default_factory=Counter)

    def add_error(self, row_number: int, message: str) -> None:
        self.error_count += 1
        if len(self.errors) < MAX_ERRORS_LISTED:
            self.errors.append({"row": row_number, "message": message})

    def as_dict(self) -> dict[str, Any]:
        return {
            "rows_read": self.rows_read,
            "rows_valid": self.rows_valid,
            "error_count": self.error_count,
            "errors": self.errors,
            "totals": {k: str(v) for k, v in self.totals.items()},
            "first_date": self.first_date.isoformat() if self.first_date else None,
            "last_date": self.last_date.isoformat() if self.last_date else None,
            "billing_periods": dict(sorted(self.periods.items())),
        }


def convert(
    rows: Iterator[dict[str, Any]],
    *,
    detected: str,
    mapping: dict[str, Any],
    opts: Options,
    limit: int | None = None,
) -> tuple[list[CostLine], Report]:
    report = Report()
    lines: list[CostLine] = []
    for number, row in enumerate(rows, start=2):  # row 1 is the header
        if limit is not None and report.rows_read >= limit:
            break
        report.rows_read += 1
        try:
            line = (
                focus.row_to_line(row, default_provider=opts.provider)
                if detected == "focus"
                else custom_row_to_line(row, mapping, opts)
            )
            line.validate()
        except (ValueProblem, CostDataError, KeyError, TypeError) as exc:
            report.add_error(number, str(exc))
            continue
        report.rows_valid += 1
        report.totals[line.billing_currency] += line.billed_cost
        report.first_date = min(filter(None, [report.first_date, line.charge_date]))
        report.last_date = max(filter(None, [report.last_date, line.charge_date]))
        report.periods[month_start(line.charge_date).isoformat()[:7]] += 1
        lines.append(line)
    return lines, report


def check_mapping(detected: str, mapping: dict[str, Any], opts: Options) -> None:
    if detected == "focus":
        return
    missing = [f for f in ("charge_date", "cost", "service_name") if not mapping.get(f)]
    if missing:
        raise AppError(
            "Map these columns first: " + ", ".join(missing) + ".", code="mapping_incomplete"
        )
    if not mapping.get("currency") and not opts.currency:
        raise AppError(
            "Map a currency column or choose a default currency.", code="mapping_incomplete"
        )


def run_import(db: Session, storage: Storage, import_id: Any) -> FileImport:
    set_tenant(db, bypass=True)
    record = db.get(FileImport, import_id)
    if record is None or not record.storage_key:
        raise AppError("Upload not found.", status=404, code="not_found")
    org_id = record.org_id
    stored = record.options or {}
    opts = Options(
        provider=stored.get("provider") or "other",
        currency=stored.get("currency"),
        date_format=stored.get("date_format"),
    )
    record.status = "importing"
    db.flush()
    rows = iter_rows(storage, record.storage_key, record.file_type)
    lines, report = convert(
        rows, detected=record.detected_format, mapping=record.mapping or {}, opts=opts
    )
    summary = report.as_dict()
    allow_partial = bool((record.options or {}).get("allow_partial"))
    failure_rate = report.error_count / report.rows_read if report.rows_read else 0
    if report.rows_valid == 0 or (failure_rate > FAILURE_THRESHOLD and not allow_partial):
        set_tenant(db, bypass=True)
        record.status, record.report = "failed", {**summary, "refused": True}
        db.flush()
        return record
    by_period: dict[date, list[CostLine]] = defaultdict(list)
    for line in lines:
        by_period[month_start(line.charge_date)].append(line)
    for period, period_lines in sorted(by_period.items()):
        replace_billing_period(
            db,
            org_id=org_id,
            source_kind="file",
            source_key=f"file:{record.id}",
            billing_period=period,
            lines=focus.aggregate_daily(period_lines),
        )
    set_tenant(db, bypass=True)
    record.status, record.report = "imported", summary
    record.rows_imported, record.imported_at = report.rows_valid, utcnow()
    db.flush()
    return record
