"""FOCUS export: a month of cost line items as CSV with FinOps FOCUS (1.2) column names.

Amounts stay in the billing currency, as FOCUS expects. Columns CostLens does not hold
are left out rather than filled with guesses. Columns of its own start with ``x_``. Rows
are streamed in batches, so a big month does not have to fit in memory."""

from __future__ import annotations

import csv
import io
import json
import uuid
from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from costlens.models import CostLineItem

COLUMNS = [
    "BilledCost",
    "BillingAccountId",
    "BillingCurrency",
    "BillingPeriodStart",
    "BillingPeriodEnd",
    "ChargeCategory",
    "ChargePeriodStart",
    "ChargePeriodEnd",
    "CommitmentDiscountId",
    "CommitmentDiscountType",
    "ConsumedQuantity",
    "ConsumedUnit",
    "EffectiveCost",
    "ListCost",
    "PricingCategory",
    "ProviderName",
    "RegionId",
    "AvailabilityZone",
    "ResourceId",
    "ResourceName",
    "ResourceType",
    "ServiceCategory",
    "ServiceName",
    "SkuId",
    "SubAccountId",
    "SubAccountName",
    "Tags",
    "x_UsageType",
    "x_Operation",
]
PROVIDERS = {"aws": "AWS", "azure": "Microsoft", "gcp": "Google Cloud", "k8s": "Kubernetes"}
BATCH = 5000


def _ts(d: date) -> str:
    return f"{d.isoformat()}T00:00:00Z"


def _next_month(d: date) -> date:
    return (d.replace(day=28) + timedelta(days=4)).replace(day=1)


def _num(v: Any) -> str:
    return "" if v is None else format(v, "f")


def row(li: CostLineItem) -> list[str]:
    return [
        _num(li.billed_cost),
        li.billing_account_id or "",
        li.billing_currency,
        _ts(li.billing_period),
        _ts(_next_month(li.billing_period)),
        li.charge_category,
        _ts(li.charge_date),
        _ts(li.charge_date + timedelta(days=1)),
        li.commitment_discount_id or "",
        li.commitment_discount_type or "",
        _num(li.consumed_quantity),
        li.consumed_unit or "",
        _num(li.effective_cost),
        _num(li.list_cost),
        li.pricing_category or "",
        PROVIDERS.get(li.provider, li.provider),
        li.region or "",
        li.availability_zone or "",
        li.resource_id or "",
        li.resource_name or "",
        li.resource_type or "",
        li.service_category or "",
        li.service_name,
        li.sku_id or "",
        li.sub_account_id or "",
        li.sub_account_name or "",
        json.dumps(li.tags or {}, sort_keys=True, separators=(",", ":")),
        li.usage_type or "",
        li.operation or "",
    ]


def stream(db: Session, org_id: uuid.UUID, month: date) -> Iterator[bytes]:
    """CSV chunks for every line item charged in ``month`` (header first)."""
    start, end = month.replace(day=1), _next_month(month)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(COLUMNS)
    stmt = (
        select(CostLineItem)
        .where(
            CostLineItem.org_id == org_id,
            CostLineItem.charge_date >= start,
            CostLineItem.charge_date < end,
        )
        .order_by(CostLineItem.charge_date, CostLineItem.id)
        .execution_options(yield_per=BATCH)
    )
    for n, li in enumerate(db.scalars(stmt), start=1):
        w.writerow(row(li))
        if n % BATCH == 0:
            yield buf.getvalue().encode()
            buf.seek(0)
            buf.truncate()
    yield buf.getvalue().encode()
