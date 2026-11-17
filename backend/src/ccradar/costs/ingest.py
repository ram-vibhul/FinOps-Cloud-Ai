"""Write cost data: replace a billing period from one source, then refresh the rollup.

Imports are idempotent: importing the same billing period from the same source
again replaces the previous rows (providers restate bills during the month).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from itertools import islice

from sqlalchemy import delete, func, insert, literal, select, text
from sqlalchemy.orm import Session

from ccradar.db.base import Base, utcnow
from ccradar.db.tenant import set_tenant
from ccradar.models import CostImportRun

CHARGE_CATEGORIES = frozenset({"Usage", "Purchase", "Tax", "Credit", "Adjustment"})
PRICING_CATEGORIES = frozenset({"Standard", "Committed", "Dynamic", "Other"})
BATCH = 5000


class CostDataError(ValueError):
    pass


@dataclass
class CostLine:
    charge_date: date
    provider: str
    service_name: str
    billed_cost: Decimal
    effective_cost: Decimal
    billing_currency: str
    charge_category: str = "Usage"
    pricing_category: str | None = "Standard"
    billing_account_id: str | None = None
    sub_account_id: str | None = None
    sub_account_name: str | None = None
    service_category: str | None = None
    region: str | None = None
    availability_zone: str | None = None
    resource_id: str | None = None
    resource_name: str | None = None
    resource_type: str | None = None
    sku_id: str | None = None
    usage_type: str | None = None
    operation: str | None = None
    commitment_discount_id: str | None = None
    commitment_discount_type: str | None = None
    consumed_quantity: Decimal | None = None
    consumed_unit: str | None = None
    list_cost: Decimal | None = None
    tags: dict[str, str] = field(default_factory=dict)
    extras: dict[str, object] = field(default_factory=dict)

    def validate(self) -> None:
        if self.charge_category not in CHARGE_CATEGORIES:
            raise CostDataError(f"unknown charge category {self.charge_category!r}")
        if self.pricing_category is not None and self.pricing_category not in PRICING_CATEGORIES:
            raise CostDataError(f"unknown pricing category {self.pricing_category!r}")
        if len(self.billing_currency) != 3 or not self.billing_currency.isupper():
            raise CostDataError(f"currency must be a 3-letter code, got {self.billing_currency!r}")
        if not self.service_name:
            raise CostDataError("service name is required")


def month_start(day: date) -> date:
    return day.replace(day=1)


def _batches(rows: Iterable[CostLine], size: int) -> Iterator[list[CostLine]]:
    it = iter(rows)
    while chunk := list(islice(it, size)):
        yield chunk


def replace_billing_period(
    db: Session,
    *,
    org_id: uuid.UUID,
    source_kind: str,
    source_key: str,
    billing_period: date,
    lines: Iterable[CostLine],
) -> CostImportRun:
    """Replace all rows of (source, billing period) with ``lines`` and refresh rollups."""
    set_tenant(db, org_id=org_id, bypass=False)
    billing_period = month_start(billing_period)
    from ccradar.models import MonthClose

    if db.scalar(select(MonthClose.id).where(MonthClose.month == billing_period)) is not None:
        # A closed month is locked: keep its numbers; reopen it to re-import.
        skipped = CostImportRun(
            org_id=org_id,
            source_kind=source_kind,
            source_key=source_key,
            billing_period=billing_period,
            status="skipped_closed",
            started_at=utcnow(),
            finished_at=utcnow(),
        )
        db.add(skipped)
        db.flush()
        return skipped
    run = CostImportRun(
        org_id=org_id,
        source_kind=source_kind,
        source_key=source_key,
        billing_period=billing_period,
        status="running",
        started_at=utcnow(),
    )
    db.add(run)
    db.flush()
    table = Base.metadata.tables["cost_line_items"]
    scope = (
        table.c.org_id == org_id,
        table.c.source_key == source_key,
        table.c.billing_period == billing_period,
    )
    old_range = db.execute(
        select(func.min(table.c.charge_date), func.max(table.c.charge_date)).where(*scope)
    ).one()
    db.execute(delete(table).where(*scope))

    from ccradar.costs.tagrules import apply_rules, org_rules
    from ccradar.models import Organization

    rules = org_rules(db.get(Organization, org_id))
    from ccradar.costs import vtags as vt

    definitions = vt.load(db, org_id)
    count, total, currencies = 0, Decimal(0), set()
    first: date | None = old_range[0]
    last: date | None = old_range[1]
    for chunk in _batches(lines, BATCH):
        rows = []
        for line in chunk:
            line.validate()
            if rules:
                line.tags = apply_rules(line.tags, rules)
            values = asdict(line)
            values["vtags"] = vt.compute(definitions, values) if definitions else {}
            values.update(
                id=uuid.uuid4(),
                org_id=org_id,
                billing_period=billing_period,
                source_key=source_key,
                import_run_id=run.id,
            )
            rows.append(values)
            total += line.effective_cost
            currencies.add(line.billing_currency)
            first = line.charge_date if first is None else min(first, line.charge_date)
            last = line.charge_date if last is None else max(last, line.charge_date)
        db.execute(insert(table), rows)
        count += len(rows)

    if first is not None and last is not None:
        refresh_daily(db, org_id=org_id, start=first, end=last)
    run.status = "succeeded"
    run.rows = count
    run.total_cost = total
    run.currency = currencies.pop() if len(currencies) == 1 else None
    run.finished_at = utcnow()
    db.flush()
    return run


DAILY_KEYS = (
    "charge_date",
    "provider",
    "sub_account_id",
    "service_name",
    "region",
    "charge_category",
    "pricing_category",
    "billing_currency",
)


TAG_DIMENSIONS = (
    "provider",
    "sub_account_id",
    "service_name",
    "region",
    "charge_category",
    "pricing_category",
    "billing_currency",
)


def _months(start: date, end: date) -> list[tuple[date, date]]:
    """``start..end`` cut at month ends."""
    out, first = [], start
    while first <= end:
        nxt = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
        out.append((first, min(nxt - timedelta(days=1), end)))
        first = nxt
    return out


def refresh_tag_daily(db: Session, *, org_id: uuid.UUID, start: date, end: date) -> int:
    """Rebuild the per-tag daily rollup (``cost_daily_tags``) for ``start..end`` from
    line items, a month per statement: one statement over a year of a 50-million-line
    bill ran the database server out of memory (11.18)."""
    return sum(
        _refresh_tag_month(db, org_id=org_id, start=a, end=b) for a, b in _months(start, end)
    )


def _refresh_tag_month(db: Session, *, org_id: uuid.UUID, start: date, end: date) -> int:
    postgres = db.get_bind().dialect.name == "postgresql"
    tags = Base.metadata.tables["cost_daily_tags"]
    db.execute(
        delete(tags).where(
            tags.c.org_id == org_id, tags.c.charge_date >= start, tags.c.charge_date <= end
        )
    )
    dims = ", ".join(f"li.{d}" for d in TAG_DIMENSIONS)
    new_id = "gen_random_uuid()" if postgres else "lower(hex(randomblob(16)))"
    value = "left(t.value, 300)" if postgres else "substr(CAST(t.value AS TEXT), 1, 300)"
    added = 0
    for column, prefix in (("tags", "tag:"), ("vtags", "vtag:")):
        each = (
            f"json_each_text(li.{column}) AS t(key, value)"
            if postgres
            else f"json_each(li.{column}) AS t"
        )
        sql = (
            "INSERT INTO cost_daily_tags (id, org_id, charge_date, tag_key, tag_value, "  # noqa: S608 - fixed names
            f"{', '.join(TAG_DIMENSIONS)}, billed_cost, effective_cost, list_cost) "
            f"SELECT {new_id}, li.org_id, li.charge_date, :prefix || t.key, {value}, {dims}, "
            "coalesce(sum(li.billed_cost), 0), coalesce(sum(li.effective_cost), 0), "
            f"sum(li.list_cost) FROM cost_line_items AS li, {each} "
            "WHERE li.org_id = :org AND li.charge_date >= :start AND li.charge_date <= :end "
            "AND length(t.key) <= 130 "
            f"GROUP BY li.org_id, li.charge_date, t.key, {value}, {dims}"
        )
        params: dict[str, object] = {"prefix": prefix, "org": org_id, "start": start, "end": end}
        if not postgres:  # SQLite stores UUIDs as hex and dates as ISO text
            params |= {"org": org_id.hex, "start": start.isoformat(), "end": end.isoformat()}
        result = db.execute(text(sql), params)
        added += int(getattr(result, "rowcount", 0) or 0)
    return added


def refresh_daily(db: Session, *, org_id: uuid.UUID, start: date, end: date) -> int:
    """Rebuild the daily rollups (totals and per tag) for ``start..end`` (inclusive)
    from line items, a month at a time."""
    refresh_tag_daily(db, org_id=org_id, start=start, end=end)
    return sum(_refresh_totals(db, org_id=org_id, start=a, end=b) for a, b in _months(start, end))


def _refresh_totals(db: Session, *, org_id: uuid.UUID, start: date, end: date) -> int:
    li = Base.metadata.tables["cost_line_items"]
    daily = Base.metadata.tables["cost_daily"]
    db.execute(
        delete(daily).where(
            daily.c.org_id == org_id, daily.c.charge_date >= start, daily.c.charge_date <= end
        )
    )
    keys = [li.c[k] for k in DAILY_KEYS]
    sums = (
        func.coalesce(func.sum(li.c.billed_cost), 0),
        func.coalesce(func.sum(li.c.effective_cost), 0),
        func.sum(li.c.list_cost),
        func.count(),
    )
    where = (li.c.org_id == org_id, li.c.charge_date >= start, li.c.charge_date <= end)
    if db.get_bind().dialect.name == "postgresql":
        # One statement on the server (11.18): a 50-million-line bill no longer passes
        # every rollup row through Python (200,000 lines took 5 s that way).
        grouped_on_server = (
            select(func.gen_random_uuid(), literal(org_id, type_=daily.c.org_id.type), *keys, *sums)
            .where(*where)
            .group_by(*keys)
        )
        target = ["id", "org_id", *DAILY_KEYS,
                  "billed_cost", "effective_cost", "list_cost", "line_count"]  # fmt: skip
        result = db.execute(insert(daily).from_select(target, grouped_on_server))
        return int(getattr(result, "rowcount", 0) or 0)
    grouped = db.execute(select(*keys, *sums).where(*where).group_by(*keys)).all()
    rows = []
    n = len(DAILY_KEYS)
    for raw in grouped:
        row = tuple(raw)
        billed, effective, listed, count = row[n:]
        rows.append(
            {
                **dict(zip(DAILY_KEYS, row[:n], strict=True)),
                "id": uuid.uuid4(),
                "org_id": org_id,
                "billed_cost": billed or 0,
                "effective_cost": effective or 0,
                "list_cost": listed,
                "line_count": count,
            }
        )
    if rows:
        db.execute(insert(daily), rows)
    return len(rows)


def refresh_all_tag_daily(db: Session, org_id: uuid.UUID) -> int:
    """After tags change on existing lines (tag rules, virtual tags): rebuild the whole
    per-tag rollup of the organisation."""
    li = Base.metadata.tables["cost_line_items"]
    first, last = db.execute(
        select(func.min(li.c.charge_date), func.max(li.c.charge_date)).where(li.c.org_id == org_id)
    ).one()
    if first is None:
        return 0
    return refresh_tag_daily(db, org_id=org_id, start=first, end=last)
