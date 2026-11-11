"""FinOps FOCUS (1.0-1.2) rows -> canonical cost lines, and daily aggregation."""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from typing import Any

from ccradar.costs.ingest import CostLine
from ccradar.costs.parsing import parse_date, parse_decimal, parse_tags

REQUIRED = ("ChargePeriodStart", "BilledCost", "BillingCurrency", "ServiceName")
CHARGE_CATEGORIES = {
    "usage": "Usage",
    "purchase": "Purchase",
    "tax": "Tax",
    "credit": "Credit",
    "adjustment": "Adjustment",
}
PRICING_CATEGORIES = {
    "standard": "Standard",
    "on-demand": "Standard",
    "committed": "Committed",
    "commitment-based": "Committed",
    "dynamic": "Dynamic",
    "other": "Other",
}


def is_focus(columns: Iterable[str]) -> bool:
    names = set(columns)
    return all(c in names for c in REQUIRED)


def _get(row: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return value
    return None


def row_to_line(row: dict[str, Any], *, default_provider: str = "other") -> CostLine:
    billed = parse_decimal(row.get("BilledCost"))
    effective_raw = _get(row, "EffectiveCost")
    charge = CHARGE_CATEGORIES.get(str(_get(row, "ChargeCategory") or "usage").lower(), "Usage")
    pricing_raw = _get(row, "PricingCategory")
    pricing = (
        PRICING_CATEGORIES.get(str(pricing_raw).lower(), "Other")
        if pricing_raw
        else (None if charge in ("Tax", "Credit") else "Standard")
    )
    quantity = _get(row, "ConsumedQuantity")
    list_cost = _get(row, "ListCost")
    provider = str(_get(row, "ProviderName", "ServiceProviderName") or default_provider)
    return CostLine(
        charge_date=parse_date(row.get("ChargePeriodStart")),
        provider=provider.strip().lower().replace(" ", "_")[:30],
        billing_account_id=_str(_get(row, "BillingAccountId")),
        sub_account_id=_str(_get(row, "SubAccountId")),
        sub_account_name=_str(_get(row, "SubAccountName")),
        service_name=str(row.get("ServiceName") or "").strip()[:200],
        service_category=_str(_get(row, "ServiceCategory")),
        region=_str(_get(row, "RegionId", "Region")),
        availability_zone=_str(_get(row, "AvailabilityZone")),
        resource_id=_str(_get(row, "ResourceId")),
        resource_name=_str(_get(row, "ResourceName")),
        resource_type=_str(_get(row, "ResourceType")),
        sku_id=_str(_get(row, "SkuId")),
        usage_type=_str(_get(row, "x_UsageType", "SkuPriceDetails")),
        charge_category=charge,
        pricing_category=pricing,
        commitment_discount_id=_str(_get(row, "CommitmentDiscountId")),
        commitment_discount_type=_str(_get(row, "CommitmentDiscountType")),
        consumed_quantity=parse_decimal(quantity) if quantity is not None else None,
        consumed_unit=_str(_get(row, "ConsumedUnit")),
        billed_cost=billed,
        effective_cost=parse_decimal(effective_raw) if effective_raw is not None else billed,
        list_cost=parse_decimal(list_cost) if list_cost is not None else None,
        billing_currency=str(row.get("BillingCurrency") or "").strip().upper(),
        tags=parse_tags(row.get("Tags")),
    )


def _str(value: Any) -> str | None:
    return None if value in (None, "") else str(value).strip()


def aggregate_daily(lines: Iterable[CostLine]) -> list[CostLine]:
    """Sum lines that differ only in hour (FOCUS exports are often hourly)."""
    acc: dict[tuple[Any, ...], CostLine] = {}
    zero = Decimal(0)
    for line in lines:
        key = (
            line.charge_date,
            line.provider,
            line.billing_account_id,
            line.sub_account_id,
            line.service_name,
            line.region,
            line.availability_zone,
            line.resource_id,
            line.usage_type,
            line.sku_id,
            line.charge_category,
            line.pricing_category,
            line.commitment_discount_id,
            line.billing_currency,
            line.consumed_unit,
            tuple(sorted(line.tags.items())),
        )
        existing = acc.get(key)
        if existing is None:
            acc[key] = line
            continue
        existing.billed_cost += line.billed_cost
        existing.effective_cost += line.effective_cost
        if line.list_cost is not None:
            existing.list_cost = (existing.list_cost or zero) + line.list_cost
        if line.consumed_quantity is not None:
            existing.consumed_quantity = (
                existing.consumed_quantity or zero
            ) + line.consumed_quantity
    return list(acc.values())
