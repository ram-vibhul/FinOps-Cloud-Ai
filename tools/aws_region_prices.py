#!/usr/bin/env python3
"""Regional price levels for list-price estimates (11.5).

Reads AWS's public on-demand Linux EC2 price files (the ones aws.amazon.com/ec2/pricing
loads, about 0.5 MB per region), compares every instance type a region shares with US East
(N. Virginia), and writes each region's median price ratio to
backend/src/ccradar/data/aws_region_factors.json. Radar and the change estimator multiply
list prices by it; prices from a customer's own bill are already regional.

    python3 tools/aws_region_prices.py      # refresh the file (a few times a year)
"""

from __future__ import annotations

import gzip
import json
import statistics
import sys
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "backend/src/ccradar/data/aws_region_factors.json"
URL = (
    "https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/"
    "ec2-ondemand-without-sec-sel/{}/Linux/index.json"
)
REGIONS = {
    "us-east-1": "US East (N. Virginia)",
    "us-east-2": "US East (Ohio)",
    "us-west-1": "US West (N. California)",
    "us-west-2": "US West (Oregon)",
    "ca-central-1": "Canada (Central)",
    "sa-east-1": "South America (Sao Paulo)",
    "eu-west-1": "EU (Ireland)",
    "eu-west-2": "EU (London)",
    "eu-west-3": "EU (Paris)",
    "eu-central-1": "EU (Frankfurt)",
    "eu-north-1": "EU (Stockholm)",
    "eu-south-1": "EU (Milan)",
    "me-south-1": "Middle East (Bahrain)",
    "af-south-1": "Africa (Cape Town)",
    "ap-south-1": "Asia Pacific (Mumbai)",
    "ap-south-2": "Asia Pacific (Hyderabad)",
    "ap-southeast-1": "Asia Pacific (Singapore)",
    "ap-southeast-2": "Asia Pacific (Sydney)",
    "ap-northeast-1": "Asia Pacific (Tokyo)",
    "ap-northeast-2": "Asia Pacific (Seoul)",
    "ap-northeast-3": "Asia Pacific (Osaka)",
    "ap-east-1": "Asia Pacific (Hong Kong)",
}


def prices(location: str) -> dict[str, float]:
    url = URL.format(urllib.parse.quote(location))
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 - fixed AWS host
        raw = response.read()
    data = json.loads(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw)
    out: dict[str, float] = {}
    for table in data["regions"].values():
        for item in table.values():
            price = float(item.get("price") or 0)
            if price > 0 and item.get("Instance Type"):
                out[item["Instance Type"]] = price
    return out


def main() -> int:
    base = prices(REGIONS["us-east-1"])
    factors: dict[str, float] = {"us-east-1": 1.0}
    for code, location in REGIONS.items():
        if code == "us-east-1":
            continue
        try:
            here = prices(location)
        except Exception as exc:  # noqa: BLE001 - a region the file does not cover
            print(f"skipped {code}: {exc}", file=sys.stderr)
            continue
        ratios = [here[t] / base[t] for t in here.keys() & base.keys()]
        if len(ratios) >= 20:
            factors[code] = round(statistics.median(ratios), 3)
            print(f"{code}: {factors[code]} ({len(ratios)} instance types)")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {
                "source": "AWS public on-demand Linux EC2 prices; median ratio to us-east-1",
                "generated": datetime.now(UTC).date().isoformat(),
                "factors": dict(sorted(factors.items())),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
