"""How to reverse a costly change (11.30).

For each change Radar prices, the AWS CLI command (and a console link) that undoes it,
for a person to check and run. CloudCostRadar never runs them: its access stays
read-only. Commands are built only from identifiers that look like the resource they
should be (an instance ID must look like ``i-…``), and every value is shell-quoted, so
a crafted name in a CloudTrail record cannot turn into a different command."""

from __future__ import annotations

import re
import shlex
from collections.abc import Callable
from typing import Any

SAFE = re.compile(r"^[A-Za-z0-9._:/@+=-]{1,300}$")
REGION = re.compile(r"^[a-z]{2}(-gov)?-[a-z]+-\d$")
TYPE_CHANGE = re.compile(r"^(?P<old>[a-z0-9.\-]+) -> (?P<new>[a-z0-9.\-]+)")
Undo = dict[str, Any]


def _cli(*parts: str) -> str:
    return " ".join(shlex.quote(p) for p in parts)


def _console(region: str, service: str, fragment: str = "") -> str:
    return f"https://{region}.console.aws.amazon.com/{service}/home?region={region}{fragment}"


def _match(value: str | None, prefix: str) -> str | None:
    return value if value and SAFE.match(value) and value.startswith(prefix) else None


def _ec2_instances(rid: str | None, region: str, action: str, note: str) -> Undo | None:
    instance = _match(rid, "i-")
    if instance is None:
        return None
    return {
        "text": note,
        "command": _cli("aws", "ec2", action, "--region", region, "--instance-ids", instance),
        "console": _console(region, "ec2", f"#InstanceDetails:instanceId={instance}"),
    }


def _resize_back(rid: str | None, region: str, label: str) -> Undo | None:
    instance, m = _match(rid, "i-"), TYPE_CHANGE.match(label)
    if instance is None or m is None:
        return None
    old = m["old"]
    return {
        "text": f"Stop the instance, set its type back to {old}, then start it (it restarts).",
        "command": "\n".join(
            [
                _cli(
                    "aws", "ec2", "stop-instances", "--region", region, "--instance-ids", instance
                ),
                _cli(
                    "aws",
                    "ec2",
                    "wait",
                    "instance-stopped",
                    "--region",
                    region,
                    "--instance-ids",
                    instance,
                ),
                _cli(
                    "aws",
                    "ec2",
                    "modify-instance-attribute",
                    "--region",
                    region,
                    "--instance-id",
                    instance,
                    "--instance-type",
                    f"Value={old}",
                ),
                _cli(
                    "aws", "ec2", "start-instances", "--region", region, "--instance-ids", instance
                ),
            ]
        ),
        "console": _console(region, "ec2", f"#InstanceDetails:instanceId={instance}"),
    }


def _volume(rid: str | None, region: str, label: str) -> Undo | None:
    volume = _match(rid, "vol-")
    if volume is None:
        return None
    return {
        "text": "Delete the volume if nothing needs its data (snapshot it first if unsure).",
        "command": _cli("aws", "ec2", "delete-volume", "--region", region, "--volume-id", volume),
        "console": _console(region, "ec2", f"#VolumeDetails:volumeId={volume}"),
    }


def _bigger_volume(rid: str | None, region: str, label: str) -> Undo | None:
    volume = _match(rid, "vol-")
    return {
        "text": "EBS volumes cannot shrink. To pay less, copy the data to a new, smaller "
        "volume and delete this one; a change of type (for example io2 to gp3) can be "
        "undone with modify-volume.",
        "console": _console(region, "ec2", f"#VolumeDetails:volumeId={volume}" if volume else ""),
    }


def _nat(rid: str | None, region: str, label: str) -> Undo | None:
    nat = _match(rid, "nat-")
    if nat is None:
        return None
    return {
        "text": "Delete the NAT gateway once nothing routes through it, then release its "
        "Elastic IP address.",
        "command": _cli(
            "aws", "ec2", "delete-nat-gateway", "--region", region, "--nat-gateway-id", nat
        ),
        "console": _console(region, "vpcconsole", f"#NatGatewayDetails:natGatewayId={nat}"),
    }


def _address(rid: str | None, region: str, label: str) -> Undo | None:
    allocation = _match(rid, "eipalloc-")
    if allocation is None:
        return None
    return {
        "text": "Release the address if nothing uses it (a released address cannot be got back).",
        "command": _cli(
            "aws", "ec2", "release-address", "--region", region, "--allocation-id", allocation
        ),
        "console": _console(region, "ec2", "#Addresses:"),
    }


def _database(rid: str | None, region: str, label: str) -> Undo | None:
    name = rid if rid and SAFE.match(rid) and not rid.startswith("arn:") else None
    if name is None:
        return None
    return {
        "text": "Delete the database, keeping a final snapshot so it can be restored.",
        "command": _cli(
            "aws",
            "rds",
            "delete-db-instance",
            "--region",
            region,
            "--db-instance-identifier",
            name,
            "--final-db-snapshot-identifier",
            f"{name}-final",
        ),
        "console": _console(region, "rds", f"#database:id={name}"),
    }


def _database_back(rid: str | None, region: str, label: str) -> Undo | None:
    name = rid if rid and SAFE.match(rid) and not rid.startswith("arn:") else None
    m = TYPE_CHANGE.match(label)
    if name is None or m is None:
        return None
    return {
        "text": f"Set the class back to {m['old']} (applied in the next maintenance window "
        "unless you add --apply-immediately, which restarts the database).",
        "command": _cli(
            "aws",
            "rds",
            "modify-db-instance",
            "--region",
            region,
            "--db-instance-identifier",
            name,
            "--db-instance-class",
            m["old"],
        ),
        "console": _console(region, "rds", f"#database:id={name}"),
    }


def _load_balancer(rid: str | None, region: str, label: str) -> Undo | None:
    arn = _match(rid, "arn:aws:elasticloadbalancing:")
    if arn is None:
        return None
    return {
        "text": "Delete the load balancer once no traffic or DNS name points at it.",
        "command": _cli(
            "aws", "elbv2", "delete-load-balancer", "--region", region, "--load-balancer-arn", arn
        ),
        "console": _console(region, "ec2", "#LoadBalancers:"),
    }


def _cache(rid: str | None, region: str, label: str) -> Undo | None:
    name = rid if rid and SAFE.match(rid) and not rid.startswith("arn:") else None
    if name is None:
        return None
    return {
        "text": "Delete the cache cluster (its contents are lost).",
        "command": _cli(
            "aws",
            "elasticache",
            "delete-cache-cluster",
            "--region",
            region,
            "--cache-cluster-id",
            name,
        ),
        "console": _console(region, "elasticache", "#/redis"),
    }


def _eks(rid: str | None, region: str, label: str) -> Undo | None:
    return {
        "text": "Delete the node group or cluster in the EKS console (node groups first; "
        "deleting a cluster removes everything running on it).",
        "console": _console(region, "eks", "#/clusters"),
    }


def _name(rid: str | None) -> str | None:
    return rid if rid and SAFE.match(rid) and not rid.startswith("arn:") else None


def _redshift(rid: str | None, region: str, label: str) -> Undo | None:
    name = _name(rid)
    if name is None:
        return None
    return {
        "text": "Delete the cluster with a final snapshot, or pause it (storage is still "
        "billed while paused).",
        "command": _cli(
            "aws",
            "redshift",
            "delete-cluster",
            "--region",
            region,
            "--cluster-identifier",
            name,
            "--final-cluster-snapshot-identifier",
            f"{name}-final",
        ),
        "console": _console(region, "redshiftv2", "#clusters"),
    }


def _redshift_back(rid: str | None, region: str, label: str) -> Undo | None:
    name = _name(rid)
    m = re.match(r"^(?P<n>\d+) x (?P<t>[a-z0-9.]+) -> ", label)
    if name is None or m is None:
        return None
    return {
        "text": f"Resize back to {m['n']} x {m['t']} (an elastic resize takes minutes).",
        "command": _cli(
            "aws",
            "redshift",
            "resize-cluster",
            "--region",
            region,
            "--cluster-identifier",
            name,
            "--node-type",
            m["t"],
            "--number-of-nodes",
            m["n"],
        ),
        "console": _console(region, "redshiftv2", "#clusters"),
    }


def _capacity_back(rid: str | None, region: str, label: str) -> Undo | None:
    name = _name(rid)
    m = re.match(r"^(?P<old>\d+) -> \d+ x ", label)
    if name is None or m is None:
        return None
    return {
        "text": f"Set the group's desired capacity back to {m['old']} (check its scaling "
        "policies, which may change it again).",
        "command": _cli(
            "aws",
            "autoscaling",
            "set-desired-capacity",
            "--region",
            region,
            "--auto-scaling-group-name",
            name,
            "--desired-capacity",
            m["old"],
        ),
        "console": _console(region, "ec2", "#AutoScalingGroups:"),
    }


# Rules that differ by service (the same event name exists in several).
BY_SOURCE: dict[tuple[str, str], Callable[[str | None, str, str], Undo | None]] = {
    ("redshift", "CreateCluster"): _redshift,
    ("redshift", "ResizeCluster"): _redshift_back,
    ("redshift", "ModifyCluster"): _redshift_back,
    ("redshift", "ResumeCluster"): _redshift,
    ("autoscaling", "UpdateAutoScalingGroup"): _capacity_back,
    ("autoscaling", "SetDesiredCapacity"): _capacity_back,
}

RULES: dict[str, Callable[[str | None, str, str], Undo | None]] = {
    "RunInstances": lambda r, g, lb: _ec2_instances(
        r, g, "terminate-instances", "Terminate the instance if it is not needed (or stop it "
        "to keep its disks; disks are still charged)."),
    "StartInstances": lambda r, g, lb: _ec2_instances(
        r, g, "stop-instances", "Stop the instance again (its disks are still charged)."),
    "ModifyInstanceAttribute": _resize_back,
    "CreateVolume": _volume,
    "ModifyVolume": _bigger_volume,
    "CreateNatGateway": _nat,
    "AllocateAddress": _address,
    "CreateDBInstance": _database,
    "ModifyDBInstance": _database_back,
    "CreateLoadBalancer": _load_balancer,
    "CreateCacheCluster": _cache,
    "CreateNodegroup": _eks,
    "CreateCluster": _eks,
}  # fmt: skip


def undo(event_name: str, resource_id: str | None, region: str | None,
         monthly: float, label: str | None, source: str = "") -> Undo | None:  # fmt: skip
    """How to reverse a change that added cost; None for savings or when unknown.
    ``source`` is the CloudTrail event source (``redshift.amazonaws.com``)."""
    service = source.split(".", 1)[0]
    rule = BY_SOURCE.get((service, event_name))
    if rule is None and service in ("", "ec2", "rds", "elasticloadbalancing", "elasticache", "eks"):
        rule = RULES.get(event_name)
    if rule is None or monthly <= 0 or not region or not REGION.match(region):
        return None
    return rule(resource_id, region, label or "")
