"""Carbon reporting (11.26): estimated emissions of cloud use, from the bill.

Method, after the open Cloud Carbon Footprint project (Apache-2.0):

- **compute**: instance hours (``BoxUsage``, ``SpotUsage``, RDS ``InstanceUsage``) times
  the instance's vCPUs times the average power per vCPU at 50% utilisation;
- **storage**: GB-months of disks and object storage times watt-hours per TB-hour (SSD
  or HDD);
- energy times the data-centre overhead (PUE) times the grid intensity of the region;
- everything else, and other clouds, is estimated from spend with one factor and shown
  separately, so a reader always sees how much is measured from use.

Embodied emissions (making the hardware) are not included. These are estimates; the
cloud providers' own carbon tools are the reference for official reporting.
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ccradar.db.base import Base

# Cloud Carbon Footprint coefficients for AWS (approximate; please verify periodically).
WATTS_PER_VCPU = 0.74 + 0.5 * (3.5 - 0.74)  # min + 50% of (max - min)
SSD_WH_PER_TB_HOUR = 1.2
HDD_WH_PER_TB_HOUR = 0.65
PUE = 1.135
HOURS_PER_MONTH = 730.0
SPEND_KG_PER_USD = 0.18  # fallback for services without a usage measure
# Grid intensity, grams CO2e per kWh, by AWS region.
GRID = {
    "us-east-1": 379.1,
    "us-east-2": 410.6,
    "us-west-1": 322.2,
    "us-west-2": 322.2,
    "ca-central-1": 120.0,
    "sa-east-1": 74.0,
    "eu-west-1": 278.6,
    "eu-west-2": 225.0,
    "eu-west-3": 51.1,
    "eu-central-1": 338.0,
    "eu-north-1": 8.8,
    "eu-south-1": 233.0,
    "ap-south-1": 708.2,
    "ap-southeast-1": 408.0,
    "ap-southeast-2": 790.0,
    "ap-northeast-1": 465.8,
    "ap-northeast-2": 415.6,
    "ap-east-1": 710.0,
    "me-south-1": 732.0,
    "af-south-1": 900.6,
}
WORLD_GRID = 475.0
INSTANCE = re.compile(
    r"(?:BoxUsage|SpotUsage|InstanceUsage|Multi-AZUsage)[^:]*:(?:db\.)?([a-z0-9-]+)\.([a-z0-9]+)$"
)
STORAGE = re.compile(r"(VolumeUsage|TimedStorage|SnapshotUsage)")
SIZES = {"nano": 1, "micro": 1, "small": 1, "medium": 1, "large": 2, "xlarge": 4}


def vcpus(size: str) -> int | None:
    if size in SIZES:
        return SIZES[size]
    m = re.match(r"^(\d+)xlarge$", size)
    return int(m[1]) * 4 if m else None


def _kg(kwh: float, region: str | None) -> float:
    return kwh * PUE * GRID.get(region or "", WORLD_GRID) / 1000


def report(db: Session, org_id: uuid.UUID, months: int, today: date) -> dict[str, Any]:
    first = (today.replace(day=1) - timedelta(days=31 * (months - 1))).replace(day=1)
    li = Base.metadata.tables["cost_line_items"]
    month = func.extract("year", li.c.charge_date) * 100 + func.extract("month", li.c.charge_date)
    rows = db.execute(
        select(
            month,
            li.c.provider,
            li.c.service_name,
            li.c.region,
            li.c.usage_type,
            func.sum(li.c.consumed_quantity),
            func.sum(li.c.effective_cost),
            li.c.billing_currency,
        )
        .where(
            li.c.org_id == org_id,
            li.c.charge_date >= first,
            li.c.charge_date <= today,
            li.c.charge_category == "Usage",
        )
        .group_by(
            month,
            li.c.provider,
            li.c.service_name,
            li.c.region,
            li.c.usage_type,
            li.c.billing_currency,
        )
    ).all()
    per_month: dict[str, dict[str, float]] = defaultdict(lambda: {"usage": 0.0, "spend": 0.0})
    per_service: dict[str, float] = defaultdict(float)
    per_region: dict[str, float] = defaultdict(float)
    for m, provider, service, region, usage_type, quantity, cost, currency in rows:
        key = f"{int(m) // 100:04d}-{int(m) % 100:02d}"
        kg, basis = None, "spend"
        usage = str(usage_type or "")
        q = float(quantity or 0)
        if provider == "aws" and q > 0:
            inst = INSTANCE.search(usage)
            if inst and (n := vcpus(inst[2])):
                kg, basis = _kg(q * n * WATTS_PER_VCPU / 1000, region), "usage"
            elif STORAGE.search(usage):
                wh = (
                    SSD_WH_PER_TB_HOUR
                    if "SSD" in usage or "gp" in usage or "io" in usage
                    else HDD_WH_PER_TB_HOUR
                )
                kg, basis = _kg(q / 1000 * wh * HOURS_PER_MONTH / 1000, region), "usage"
        if kg is None:
            usd = float(cost or 0) if currency == "USD" else 0.0  # other currencies: not counted
            kg = max(usd, 0.0) * SPEND_KG_PER_USD
        per_month[key][basis] += kg
        per_service[str(service)] += kg
        per_region[str(region or "global")] += kg
    total = sum(v["usage"] + v["spend"] for v in per_month.values())
    measured = sum(v["usage"] for v in per_month.values())

    def top(d: dict[str, float]) -> list[dict[str, Any]]:
        return [
            {"name": k, "kg": round(v, 1)} for k, v in sorted(d.items(), key=lambda x: -x[1])[:10]
        ]

    return {
        "unit": "kg CO2e",
        "months": [
            {"month": k, "from_usage": round(v["usage"], 1), "from_spend": round(v["spend"], 1)}
            for k, v in sorted(per_month.items())
        ],
        "total_kg": round(total, 1),
        "measured_share": round(100 * measured / total, 1) if total else None,
        "by_service": top(per_service),
        "by_region": top(per_region),
        "method": "Cloud Carbon Footprint coefficients (compute and storage use, PUE, grid "
        "intensity); other spend at 0.18 kg CO2e per US dollar. Excludes embodied emissions.",
    }
