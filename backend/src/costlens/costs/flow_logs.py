"""Network cost attribution from VPC Flow Logs (read-only).

A day of flow records is sampled from the customer's log destination (S3 or CloudWatch
Logs). Every record is classified by where it was seen and where it went:

- seen on a NAT gateway's interface, with a private address on the other side:
  NAT processing, charged to that private address;
- seen on any other interface, leaving to a public address: internet transfer;
- between private addresses in different Availability Zones: cross-zone transfer, each
  side charged for its own direction (both sides pay);
- to a private address CostLens knows in another region: inter-region transfer.

Addresses are named by the most specific owner known: a Kubernetes workload (pod IP), an
EC2 instance (its tags give the team), an AWS service interface (load balancer, RDS...),
or the bare address. Each category's billed monthly cost is then shared out by bytes,
so the figures add up to the bill."""

from __future__ import annotations

import gzip
import ipaddress
import uuid
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from costlens.costs.network import CATEGORIES, network_costs
from costlens.db.base import utcnow
from costlens.inventory.aws import _pages, _tags
from costlens.models import CloudResource, K8sCluster, K8sObject, NetworkAttribution

DEFAULT_FIELDS = [
    "version", "account-id", "interface-id", "srcaddr", "dstaddr", "srcport", "dstport",
    "protocol", "packets", "bytes", "start", "end", "action", "log-status",
]  # fmt: skip
MAX_FILES = 50
MAX_BYTES = 200 * 2**20
MAX_EVENTS = 200_000
ATTRIBUTED = ("nat_processing", "cross_az", "internet_out", "inter_region")
TEAM_TAGS = ("team", "owner", "app", "application", "service")
PRIVATE_EXTRA = (ipaddress.ip_network("100.64.0.0/10"),)


@dataclass
class Flow:
    interface: str | None
    src: str
    dst: str
    bytes: int
    action: str


@dataclass
class Owner:
    key: str
    label: str
    kind: str  # workload | instance | service | ip
    team: str | None = None
    az: str | None = None
    region: str | None = None


def is_private(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return ip.is_private or any(ip in net for net in PRIVATE_EXTRA)


def parse(lines: Iterable[str]) -> Iterator[Flow]:
    """Default or custom format; a header line (as S3 files have) names the fields."""
    fields = DEFAULT_FIELDS
    for raw in lines:
        parts = raw.split()
        if not parts:
            continue
        if parts[0] in ("version", "account-id", "interface-id", "srcaddr"):
            fields = parts
            continue
        row = dict(zip(fields, parts, strict=False))
        src = row.get("pkt-srcaddr") if row.get("pkt-srcaddr", "-") != "-" else row.get("srcaddr")
        dst = row.get("pkt-dstaddr") if row.get("pkt-dstaddr", "-") != "-" else row.get("dstaddr")
        size = row.get("bytes", "-")
        if not src or not dst or src == "-" or not size.isdigit():
            continue
        yield Flow(row.get("interface-id"), src, dst, int(size), row.get("action", "ACCEPT"))


class IpBook:
    """Private and public addresses mapped to their owners."""

    def __init__(self) -> None:
        self.by_ip: dict[str, Owner] = {}
        self.interfaces: dict[str, Owner] = {}
        self.nat_interfaces: set[str] = set()
        self.nat_ips: set[str] = set()

    def add_interfaces(
        self, enis: Iterable[dict[str, Any]], region: str, instances: dict[str, CloudResource]
    ) -> None:
        for e in enis:
            owner = self._owner(e, region, instances)
            self.interfaces[e["NetworkInterfaceId"]] = owner
            if e.get("InterfaceType") == "nat_gateway":
                self.nat_interfaces.add(e["NetworkInterfaceId"])
            for a in e.get("PrivateIpAddresses") or []:
                if a.get("PrivateIpAddress"):
                    self.by_ip.setdefault(a["PrivateIpAddress"], owner)
                    if e.get("InterfaceType") == "nat_gateway":
                        self.nat_ips.add(a["PrivateIpAddress"])
                public = (a.get("Association") or {}).get("PublicIp")
                if public:
                    self.by_ip.setdefault(public, owner)

    @staticmethod
    def _owner(e: dict[str, Any], region: str, instances: dict[str, CloudResource]) -> Owner:
        az = e.get("AvailabilityZone")
        instance_id = (e.get("Attachment") or {}).get("InstanceId")
        tags = _tags(e.get("TagSet"))
        if instance_id:
            inst = instances.get(instance_id)
            itags = {k.lower(): v for k, v in ((inst.tags if inst else None) or {}).items()}
            team = next((itags[k] for k in TEAM_TAGS if itags.get(k)), None)
            label = (inst.name if inst and inst.name else None) or instance_id
            return Owner(f"ec2:{instance_id}", label, "instance", team, az, region)
        description = str(e.get("Description") or e.get("InterfaceType") or e["NetworkInterfaceId"])
        team = next((v for k, v in tags.items() if k.lower() in TEAM_TAGS), None)
        return Owner(f"eni:{description[:200]}", description[:200], "service", team, az, region)

    def add_pods(self, pods: Iterable[tuple[str, str, str, str]]) -> None:
        """(pod IP, cluster, namespace, workload): pods win over their node's interface."""
        for ip, cluster, namespace, workload in pods:
            base = self.by_ip.get(ip)
            self.by_ip[ip] = Owner(
                f"k8s:{cluster}/{namespace}/{workload}",
                f"{namespace}/{workload} ({cluster})",
                "workload",
                namespace,
                base.az if base else None,
                base.region if base else None,
            )

    def owner(self, ip: str) -> Owner:
        return self.by_ip.get(ip) or Owner(f"ip:{ip}", ip, "ip")


def classify(flow: Flow, book: IpBook) -> list[tuple[str, Owner]]:  # noqa: PLR0911
    """Category and owner for each charge a record represents (may be none or two)."""
    if flow.action != "ACCEPT":
        return []
    src_private, dst_private = is_private(flow.src), is_private(flow.dst)
    if flow.interface in book.nat_interfaces:
        if src_private and flow.src not in book.nat_ips:
            return [("nat_processing", book.owner(flow.src))]
        if dst_private and flow.dst not in book.nat_ips:
            return [("nat_processing", book.owner(flow.dst))]
        return []
    eni = book.interfaces.get(flow.interface or "")
    src, dst = book.owner(flow.src), book.owner(flow.dst)
    if src_private and not dst_private:
        return [("internet_out", src)] if flow.src in book.by_ip or eni else []
    if src_private and dst_private and src.kind != "ip" and dst.kind != "ip":
        if src.region and dst.region and src.region != dst.region:
            return [("inter_region", src)]
        if src.az and dst.az and src.az != dst.az:
            # On the sender's interface the sender pays "out"; on the receiver's, "in".
            seen_on_receiver = eni is not None and flow.dst in book.by_ip and eni.key == dst.key
            return [("cross_az", dst if seen_on_receiver else src)]
    return []


def attribute(flows: Iterable[Flow], book: IpBook) -> dict[str, dict[str, Any]]:
    """{category: {owner key: {"owner": Owner, "bytes": n}}}"""
    out: dict[str, dict[str, Any]] = defaultdict(dict)
    for f in flows:
        for category, owner in classify(f, book):
            slot = out[category].setdefault(owner.key, {"owner": owner, "bytes": 0})
            slot["bytes"] += f.bytes
    return out


# ---- reading the logs ---------------------------------------------------------------------


def s3_lines(
    client: Any, bucket: str, prefix: str, account: str, region: str, day: datetime
) -> Iterator[str]:
    base = prefix.strip("/") + "/" if prefix.strip("/") else ""
    path = f"{base}AWSLogs/{account}/vpcflowlogs/{region}/{day:%Y/%m/%d}/"
    read = 0
    keys = [
        o["Key"]
        for page in _pages(
            client.list_objects_v2,
            "ContinuationToken",
            "NextContinuationToken",
            Bucket=bucket,
            Prefix=path,
        )
        for o in page.get("Contents", [])
    ][:MAX_FILES]
    for key in keys:
        import io

        buf = io.BytesIO()
        client.download_fileobj(Bucket=bucket, Key=key, Fileobj=buf)
        data = buf.getvalue()
        read += len(data)
        if key.endswith(".gz"):
            data = gzip.decompress(data)
        yield from data.decode("utf-8", "replace").splitlines()
        if read > MAX_BYTES:
            return


def cloudwatch_lines(client: Any, log_group: str, day: datetime) -> Iterator[str]:
    start = int(day.timestamp() * 1000)
    end = int((day + timedelta(days=1)).timestamp() * 1000)
    seen = 0
    for page in _pages(
        client.filter_log_events,
        "nextToken",
        "nextToken",
        logGroupName=log_group,
        startTime=start,
        endTime=end,
    ):
        for e in page.get("events", []):
            seen += 1
            yield e.get("message", "")
            if seen >= MAX_EVENTS:
                return


def discover(ec2: Any) -> list[dict[str, Any]]:
    """Flow logs configured in a region (``ec2:DescribeFlowLogs``)."""
    out = []
    for f in ec2.describe_flow_logs().get("FlowLogs", []):
        kind = f.get("LogDestinationType", "cloud-watch-logs")
        dest = f.get("LogDestination") or ""
        if kind == "s3":
            bucket_path = dest.removeprefix("arn:aws:s3:::")
            bucket, _, prefix = bucket_path.partition("/")
            out.append(
                {"type": "s3", "bucket": bucket, "prefix": prefix, "resource": f.get("ResourceId")}
            )
        elif kind == "cloud-watch-logs":
            out.append(
                {
                    "type": "cloudwatch",
                    "log_group": f.get("LogGroupName"),
                    "resource": f.get("ResourceId"),
                }
            )
    unique = {tuple(sorted((k, v) for k, v in s.items() if k != "resource")): s for s in out}
    return list(unique.values())


def policy_statements(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extra read-only statements the CostLens role needs for these sources."""
    buckets = sorted({s["bucket"] for s in sources if s.get("type") == "s3" and s.get("bucket")})
    groups = sorted(
        {s["log_group"] for s in sources if s.get("type") == "cloudwatch" and s.get("log_group")}
    )
    out: list[dict[str, Any]] = []
    if buckets:
        out.append(
            {
                "Sid": "ReadVpcFlowLogs",
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:ListBucket"],
                "Resource": [
                    r for b in buckets for r in (f"arn:aws:s3:::{b}", f"arn:aws:s3:::{b}/*")
                ],
            }
        )
    if groups:
        out.append(
            {
                "Sid": "ReadVpcFlowLogGroups",
                "Effect": "Allow",
                "Action": ["logs:FilterLogEvents"],
                "Resource": [f"arn:aws:logs:*:*:log-group:{g}:*" for g in groups],
            }
        )
    return out


def _pods(db: Session) -> list[tuple[str, str, str, str]]:
    from costlens.k8s.allocation import owner_of

    names = dict(db.execute(select(K8sCluster.id, K8sCluster.name)).all())
    live = list(
        db.scalars(
            select(K8sObject).where(
                K8sObject.deleted_at.is_(None), K8sObject.kind.in_(("Pod", "ReplicaSet", "Job"))
            )
        )
    )
    by_uid = {(o.kind, o.uid): o for o in live}
    out = []
    for p in live:
        data = p.data or {}
        if p.kind == "Pod" and data.get("podIP") and not data.get("hostNetwork"):
            _, workload = owner_of(p, by_uid)
            out.append(
                (data["podIP"], names.get(p.cluster_id, "cluster"), p.namespace or "", workload)
            )
    return out


def run(
    db: Session,
    org_id: uuid.UUID,
    currency: str,
    lines: Iterable[str],
    enis: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Attribute sampled flows and store the result (replacing the previous one)."""
    instances = {
        r.resource_id: r
        for r in db.scalars(
            select(CloudResource).where(CloudResource.resource_type == "ec2_instance")
        )
    }
    book = IpBook()
    for region, items in enis.items():
        book.add_interfaces(items, region, instances)
    book.add_pods(_pods(db))
    shares = attribute(parse(lines), book)
    costs = {
        c["key"]: c["cost"] for c in network_costs(db, org_id, currency=currency)["categories"]
    }
    now = utcnow()
    db.execute(delete(NetworkAttribution))
    rows = 0
    for category in ATTRIBUTED:
        owners = shares.get(category, {})
        total = sum(o["bytes"] for o in owners.values())
        if not total:
            continue
        for key, slot in sorted(owners.items(), key=lambda kv: -kv[1]["bytes"])[:200]:
            o: Owner = slot["owner"]
            share = slot["bytes"] / total
            db.add(
                NetworkAttribution(
                    org_id=org_id,
                    computed_at=now,
                    category=category,
                    owner_key=key[:500],
                    owner_label=o.label[:500],
                    owner_kind=o.kind,
                    team=(o.team or None),
                    bytes=float(slot["bytes"]),
                    share_pct=round(100 * share, 2),
                    cost_monthly=round(costs.get(category, 0.0) * share, 2),
                    currency=currency,
                    details={"az": o.az, "region": o.region},
                )
            )
            rows += 1
    db.flush()
    return {"rows": rows, "categories": {k: len(v) for k, v in shares.items()}}


def collect(
    db: Session, gateway: Any, connection_id: uuid.UUID, day: datetime | None = None
) -> dict[str, Any]:
    """Read yesterday's flow logs for a connection that has them turned on."""
    from costlens.clouds.aws.client import AwsAccessError
    from costlens.db.tenant import set_tenant
    from costlens.inventory.service import billed_regions
    from costlens.models import CloudConnection, Organization

    set_tenant(db, bypass=True)
    conn = db.get(CloudConnection, connection_id)
    settings: dict[str, Any] = ((conn.settings or {}).get("flow_logs") if conn else None) or {}
    if conn is None or conn.status != "active" or not conn.role_arn or not settings.get("enabled"):
        return {}
    org = db.get(Organization, conn.org_id)
    currency = org.default_currency if org else "USD"
    regions = billed_regions(db, conn.org_id, conn.account_id)
    try:
        session = gateway.assume(conn.role_arn, conn.external_id, f"costlens-{conn.org_id}")
    except AwsAccessError:
        return {"error": "access_denied"}
    day = day or datetime.combine(utcnow().date() - timedelta(days=1), datetime.min.time(), UTC)
    enis: dict[str, list[dict[str, Any]]] = {}
    lines: list[str] = []
    errors: dict[str, str] = {}
    for region in regions:
        try:
            ec2 = gateway.client(session, "ec2", region=region)
            enis[region] = [
                e
                for page in _pages(
                    ec2.describe_network_interfaces, "NextToken", "NextToken", MaxResults=1000
                )
                for e in page.get("NetworkInterfaces", [])
            ]
        except Exception as error:
            errors[f"ec2:{region}"] = type(error).__name__
    for source in settings.get("sources") or []:
        try:
            if source.get("type") == "s3":
                s3 = gateway.client(session, "s3", region=None)
                for region in regions:
                    lines += list(
                        s3_lines(
                            s3,
                            source["bucket"],
                            source.get("prefix", ""),
                            conn.account_id or "",
                            region,
                            day,
                        )
                    )
            elif source.get("type") == "cloudwatch":
                for region in regions:
                    logs = gateway.client(session, "logs", region=region)
                    lines += list(cloudwatch_lines(logs, source["log_group"], day))
        except Exception as error:
            errors[str(source.get("bucket") or source.get("log_group"))] = type(error).__name__
    set_tenant(db, org_id=conn.org_id)
    result = run(db, conn.org_id, currency, lines, enis)
    conn.settings = {
        **(conn.settings or {}),
        "flow_logs": {
            **settings,
            "last_run": utcnow().isoformat(),
            "lines": len(lines),
            "errors": errors,
        },
    }
    db.flush()
    return {**result, "lines": len(lines), "errors": errors, "labels": CATEGORIES}
