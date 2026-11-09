"""Cost impact of planned changes, before they are applied.

Inputs: a Terraform plan (``terraform show -json plan.out``) or Kubernetes manifests
(YAML or JSON; optionally the current version to compare against). Output: one line per
resource with the monthly cost before and after, and a total change.

Prices, best first:
1. the organisation's own unit prices from its bill (``prices`` mapping, from
   ``GET /api/v1/estimates/prices`` or the ``--prices`` file in CI);
2. built-in on-demand list prices (USD, us-east-1) for the common cases;
3. otherwise the resource is listed as "not priced", never guessed.

Pure functions only: no database and no network, so the CLI can run in CI offline."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

import yaml

HOURS = 730.0
# On-demand list prices, USD, us-east-1 (please verify periodically; see tracker 6.3).
EBS_GB_MONTH = {
    "gp2": 0.10,
    "gp3": 0.08,
    "io1": 0.125,
    "io2": 0.125,
    "st1": 0.045,
    "sc1": 0.015,
    "standard": 0.05,
}
RDS_GB_MONTH = {"gp2": 0.115, "gp3": 0.115, "io1": 0.125, "io2": 0.125, "standard": 0.10}
NAT_HOUR = 0.045
LB_HOUR = {"application": 0.0225, "network": 0.0225, "gateway": 0.0125}
EKS_CLUSTER_HOUR = 0.10
# Instance prices from size and family: size units (large = 2) x the family's rate per
# unit. Matches published t3/m5/c5/r5 Linux on-demand prices.
SIZES = {
    "nano": 0.125,
    "micro": 0.25,
    "small": 0.5,
    "medium": 1,
    "large": 2,
    "xlarge": 4,
    "metal": 96,
}
UNIT_HOUR = {"t": 0.0416, "m": 0.048, "c": 0.0425, "r": 0.063, "x": 0.1668, "i": 0.078, "z": 0.093}
GRAVITON_FACTOR = 0.8
DB_FACTOR = 1.78  # RDS on-demand is roughly this much above EC2 for the same size
MULTI_AZ = 2.0
CACHE_FACTOR = 2.0  # ElastiCache nodes cost about twice the same EC2 size
SPOT_FACTOR = 0.35
# Kubernetes requests (same defaults as the cluster cost model).
K8S_CPU_HOUR = 0.031611
K8S_GIB_HOUR = 0.004237
WORKLOADS = {"Deployment", "StatefulSet", "ReplicaSet", "Job", "CronJob", "Pod", "DaemonSet"}
INSTANCE = re.compile(
    r"^(?:db\.|cache\.)?([a-z]+)(\d+)([a-z-]*)\.(\d*)(nano|micro|small|medium|large|xlarge|metal)$"
)


@dataclass
class Line:
    address: str
    resource_type: str
    action: str  # create | delete | update | replace | no-op
    before: float | None
    after: float | None
    source: str  # bill | list price | requests | not priced
    note: str = ""

    @property
    def change(self) -> float:
        return round((self.after or 0.0) - (self.before or 0.0), 2)


@dataclass
class Estimate:
    currency: str
    lines: list[Line] = field(default_factory=list)

    @property
    def change(self) -> float:
        return round(sum(line.change for line in self.lines if line.source != "not priced"), 2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "currency": self.currency,
            "monthly_change": self.change,
            "before": round(sum(x.before or 0 for x in self.lines if x.source != "not priced"), 2),
            "after": round(sum(x.after or 0 for x in self.lines if x.source != "not priced"), 2),
            "not_priced": [x.address for x in self.lines if x.source == "not priced"],
            "lines": [{**asdict(x), "change": x.change} for x in self.lines],
        }


def instance_hourly(kind: str, prices: dict[str, float] | None = None) -> tuple[float, str] | None:
    """Hourly price of an EC2 / RDS / ElastiCache instance type."""
    if prices and kind in prices:
        return prices[kind], "bill"
    m = INSTANCE.match(kind)
    if not m:
        return None
    family, _gen, attrs, mult, size = m.groups()
    per_unit = UNIT_HOUR.get(family[0])
    if per_unit is None:
        return None
    units = SIZES[size] * (int(mult) if mult and size == "xlarge" else 1)
    price = units * per_unit * (GRAVITON_FACTOR if "g" in attrs else 1.0)
    if kind.startswith("db."):
        price *= DB_FACTOR
    elif kind.startswith("cache."):
        price *= CACHE_FACTOR
    return round(price, 4), "list price"


def _num(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


Priced = tuple[float, str, str] | None


def _instance(cfg: dict[str, Any], prices: dict[str, float]) -> Priced:
    p = instance_hourly(str(cfg.get("instance_type") or ""), prices)
    if p is None:
        return None
    root = (cfg.get("root_block_device") or [{}])[0] or {}
    vtype = root.get("volume_type") or "gp3"
    disk = _num(root.get("volume_size"), 8) * EBS_GB_MONTH.get(vtype, 0.08)
    return p[0] * HOURS + disk, p[1], str(cfg.get("instance_type"))


def _volume(cfg: dict[str, Any], prices: dict[str, float]) -> Priced:
    vtype = cfg.get("type") or "gp3"
    size = _num(cfg.get("size"))
    return size * EBS_GB_MONTH.get(vtype, 0.08), "list price", f"{size:g} GB {vtype}"


def _database(cfg: dict[str, Any], prices: dict[str, float]) -> Priced:
    p = instance_hourly(str(cfg.get("instance_class") or ""), prices)
    if p is None:
        return None
    az = MULTI_AZ if cfg.get("multi_az") else 1.0
    gb = _num(cfg.get("allocated_storage"))
    storage = gb * RDS_GB_MONTH.get(cfg.get("storage_type") or "gp2", 0.115)
    label = f"{cfg.get('instance_class')}{' multi-AZ' if az > 1 else ''}"
    return (p[0] * HOURS + storage) * az, p[1], label


def _cache(cfg: dict[str, Any], prices: dict[str, float]) -> Priced:
    p = instance_hourly(str(cfg.get("node_type") or ""), prices)
    if p is None:
        return None
    nodes = _num(cfg.get("num_cache_nodes"), 1)
    return p[0] * HOURS * nodes, p[1], f"{nodes:g} x {cfg.get('node_type')}"


def _load_balancer(cfg: dict[str, Any], prices: dict[str, float]) -> Priced:
    kind = cfg.get("load_balancer_type") or "application"
    return LB_HOUR.get(kind, 0.0225) * HOURS, "list price", f"{kind}; capacity units are extra"


def _node_group(cfg: dict[str, Any], prices: dict[str, float]) -> Priced:
    types = cfg.get("instance_types") or ["t3.medium"]
    p = instance_hourly(str(types[0]), prices)
    if p is None:
        return None
    scaling = (cfg.get("scaling_config") or [{}])[0] or {}
    n = _num(scaling.get("desired_size"), 1)
    spot = cfg.get("capacity_type") == "SPOT"
    label = f"{n:g} x {types[0]}{' spot (about 65% off)' if spot else ''}"
    return p[0] * HOURS * n * (SPOT_FACTOR if spot else 1.0), p[1], label


PRICERS = {
    "aws_instance": _instance,
    "aws_spot_instance_request": _instance,
    "aws_ebs_volume": _volume,
    "aws_db_instance": _database,
    "aws_rds_cluster_instance": _database,
    "aws_elasticache_cluster": _cache,
    "aws_lb": _load_balancer,
    "aws_alb": _load_balancer,
    "aws_eks_node_group": _node_group,
    "aws_nat_gateway": lambda cfg, prices: (
        NAT_HOUR * HOURS,
        "list price",
        "hourly charge only; data processed is extra",
    ),
    "aws_eks_cluster": lambda cfg, prices: (
        EKS_CLUSTER_HOUR * HOURS,
        "list price",
        "control plane",
    ),
}


def _monthly(rtype: str, cfg: dict[str, Any] | None, prices: dict[str, float]) -> Priced:
    """Monthly cost of one Terraform resource's configuration, or None if not priced."""
    if cfg is None:
        return 0.0, "", ""
    pricer = PRICERS.get(rtype)
    return pricer(cfg, prices) if pricer else None


def _action(actions: list[str]) -> str:
    if actions in (["no-op"], ["read"]):
        return "no-op"
    if "create" in actions and "delete" in actions:
        return "replace"
    return actions[0] if actions else "no-op"


def terraform(
    plan: dict[str, Any], prices: dict[str, float] | None = None, currency: str = "USD"
) -> Estimate:
    prices = prices or {}
    out = Estimate(currency)
    for rc in plan.get("resource_changes") or []:
        if rc.get("mode") == "data":
            continue
        change = rc.get("change") or {}
        action = _action(change.get("actions") or [])
        if action == "no-op":
            continue
        rtype, address = rc.get("type", ""), rc.get("address", "")
        before = _monthly(rtype, change.get("before"), prices)
        after = _monthly(rtype, change.get("after"), prices)
        if before is None or after is None:
            out.lines.append(Line(address, rtype, action, None, None, "not priced"))
            continue
        source = after[1] or before[1]
        note = after[2] or before[2]
        if before[2] and after[2] and before[2] != after[2]:
            note = f"{before[2]} → {after[2]}"
        out.lines.append(
            Line(address, rtype, action, round(before[0], 2), round(after[0], 2), source, note)
        )
    return out


def _cpu(v: Any) -> float:
    s = str(v or "0")
    return float(s[:-1]) / 1000 if s.endswith("m") else _num(s)


def _gib(v: Any) -> float:
    s = str(v or "0")
    units = {
        "Ki": 2**-20,
        "Mi": 2**-10,
        "Gi": 1,
        "Ti": 2**10,
        "K": 1e3 / 2**30,
        "M": 1e6 / 2**30,
        "G": 1e9 / 2**30,
    }
    for u, f in units.items():
        if s.endswith(u):
            return _num(s[: -len(u)]) * f
    return _num(s) / 2**30


class ManifestError(ValueError):
    pass


def _docs(text: str) -> list[dict[str, Any]]:
    text = text.strip()
    if not text:
        return []
    try:
        if text.startswith("{"):
            data = json.loads(text)
            docs = data.get("items", [data]) if isinstance(data, dict) else []
        else:
            docs = [d for d in yaml.safe_load_all(text) if isinstance(d, dict)]
    except (ValueError, yaml.YAMLError) as exc:
        raise ManifestError(str(exc)) from exc
    flat: list[dict[str, Any]] = []
    for d in docs:
        flat.extend(d.get("items") or [] if d.get("kind") == "List" else [d])
    return flat


def _workloads(
    text: str, daemon_nodes: int, rates: tuple[float, float]
) -> dict[str, tuple[float, str]]:
    out: dict[str, tuple[float, str]] = {}
    for d in _docs(text):
        kind = d.get("kind")
        if kind not in WORKLOADS:
            continue
        meta = d.get("metadata") or {}
        key = f"{kind}/{meta.get('namespace') or 'default'}/{meta.get('name')}"
        spec = d.get("spec") or {}
        if kind == "CronJob":
            pod = ((spec.get("jobTemplate") or {}).get("spec") or {}).get("template", {}).get(
                "spec"
            ) or {}
            replicas = 1.0
        elif kind == "Pod":
            pod, replicas = spec, 1.0
        else:
            pod = (spec.get("template") or {}).get("spec") or {}
            replicas = float(daemon_nodes if kind == "DaemonSet" else spec.get("replicas", 1) or 0)
        cpu = gib = 0.0
        for c in pod.get("containers") or []:
            req = (c.get("resources") or {}).get("requests") or {}
            cpu += _cpu(req.get("cpu"))
            gib += _gib(req.get("memory"))
        monthly = replicas * HOURS * (cpu * rates[0] + gib * rates[1])
        out[key] = (monthly, f"{replicas:g} x ({cpu:g} CPU, {gib:.2f} GiB)")
    return out


def kubernetes(
    after: str,
    before: str = "",
    currency: str = "USD",
    rates: tuple[float, float] | None = None,
    daemon_nodes: int = 3,
) -> Estimate:
    """Compare workloads' requested CPU and memory (priced per hour) before and after."""
    r = rates or (K8S_CPU_HOUR, K8S_GIB_HOUR)
    a, b = _workloads(after, daemon_nodes, r), _workloads(before, daemon_nodes, r)
    out = Estimate(currency)
    for key in sorted(set(a) | set(b)):
        old, new = b.get(key), a.get(key)
        action = "create" if old is None else "delete" if new is None else "update"
        if old and new and abs(old[0] - new[0]) < 0.005:
            continue
        note = new[1] if new else old[1] if old else ""
        if old and new:
            note = f"{old[1]} → {new[1]}"
        out.lines.append(
            Line(
                key,
                key.split("/", 1)[0],
                action,
                round(old[0], 2) if old else 0.0,
                round(new[0], 2) if new else 0.0,
                "requests",
                note,
            )
        )
    return out


def markdown(e: Estimate) -> str:
    d = e.to_dict()
    c = e.currency
    sign = "+" if d["monthly_change"] > 0 else ""
    rows = [
        f"### CostLens estimate: {sign}{d['monthly_change']:,.2f} {c} a month",
        "",
        f"From {d['before']:,.2f} to {d['after']:,.2f} {c} a month for the resources this "
        "change touches.",
        "",
        "| Resource | Action | Before | After | Change | Basis |",
        "|---|---|---:|---:|---:|---|",
    ]
    for x in e.lines:
        if x.source == "not priced":
            rows.append(f"| `{x.address}` | {x.action} | | | | not priced |")
            continue
        basis = x.source + (f" ({x.note})" if x.note else "")
        rows.append(
            f"| `{x.address}` | {x.action} | {x.before or 0:,.2f} | {x.after or 0:,.2f} | "
            f"{'+' if x.change > 0 else ''}{x.change:,.2f} | {basis} |"
        )
    if d["not_priced"]:
        rows += [
            "",
            f"{len(d['not_priced'])} resource(s) could not be priced and are not in the total.",
        ]
    rows += [
        "",
        "Estimates use on-demand prices before discounts unless your bill's prices were supplied.",
    ]
    return "\n".join(rows)
