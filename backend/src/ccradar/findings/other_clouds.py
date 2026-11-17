"""Azure and Google Cloud waste (11.28): disks attached to nothing and public IP addresses
used by nothing. Savings come from the bill where it names the resource, else from list
prices. Oversized machines and commitments come from Azure Advisor and Google
Recommender (``findings/advice.py``)."""

from __future__ import annotations

import shlex
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ccradar.findings.aws import Pricing
from ccradar.findings.registry import FindingIn, detector
from ccradar.models import CloudResource

# List prices, USD a month (please verify periodically).
AZURE_DISK_GB = {"premium_lrs": 0.15, "premiumv2_lrs": 0.12, "standardssd_lrs": 0.075,
                 "standard_lrs": 0.045, "ultrassd_lrs": 0.12}  # fmt: skip
AZURE_IP = {"standard": 3.65, "basic": 2.63}
GCP_DISK_GB = {"pd-standard": 0.04, "pd-balanced": 0.10, "pd-ssd": 0.17, "pd-extreme": 0.125,
               "hyperdisk-balanced": 0.08}  # fmt: skip
GCP_IP = 7.30  # an unused static external address, $0.01 an hour


def _resources(db: Session, provider: str, kind: str) -> list[CloudResource]:
    return list(db.scalars(select(CloudResource).where(
        CloudResource.provider == provider, CloudResource.resource_type == kind,
        CloudResource.state == "unattached")))  # fmt: skip


def _common(r: CloudResource, provider: str) -> dict[str, Any]:
    return {"provider": provider, "account_id": r.account_id, "region": r.region,
            "service": r.service, "resource_type": r.resource_type, "resource_id": r.resource_id}  # fmt: skip


def _disks(db: Session, org_id: uuid.UUID, currency: str, provider: str) -> list[FindingIn]:
    disks = _resources(db, provider, "managed_disk")
    pricing = Pricing(db, org_id, currency, [d.resource_id for d in disks])
    table = AZURE_DISK_GB if provider == "azure" else GCP_DISK_GB
    out = []
    for d in disks:
        a = d.attributes or {}
        size, sku = float(a.get("size_gb") or 0), str(a.get("sku") or "")
        per_gb = table.get(sku.lower() if provider == "azure" else sku, 0.08)
        priced = pricing.cost(d.resource_id, size * per_gb)
        if priced is None or priced[0] < 1:
            continue
        name = shlex.quote(d.name or "")
        if provider == "azure":
            commands = [
                f"az snapshot create --source {shlex.quote(d.resource_id)} --name {shlex.quote((d.name or 'disk') + '-before-delete')} --resource-group <group>",
                f"az disk delete --ids {shlex.quote(d.resource_id)}",
            ]  # fmt: skip
        else:
            zone = shlex.quote(str(a.get("zone") or ""))
            commands = [
                f"gcloud compute snapshots create {shlex.quote((d.name or 'disk') + '-before-delete')} --source-disk {name} --source-disk-zone {zone} --project {shlex.quote(d.account_id or '')}",
                f"gcloud compute disks delete {name} --zone {zone} --project {shlex.quote(d.account_id or '')}",
            ]  # fmt: skip
        out.append(FindingIn(
            key=d.resource_id, title=f"Delete unattached disk {d.name or d.resource_id}",
            description=f"{size:.0f} GB {sku} disk attached to nothing (cost from {priced[1]}).",
            savings_monthly=priced[0], currency=currency, risk="medium", effort="low",
            **_common(d, provider),
            steps=["Check nobody needs it (labels or tags, who detached it).",
                   "Take a snapshot first, so it can be restored.", "Delete the disk."],
            commands=commands, evidence={"size_gb": size, "sku": sku, "basis": priced[1]},
        ))  # fmt: skip
    return out


def _ips(db: Session, org_id: uuid.UUID, currency: str, provider: str) -> list[FindingIn]:
    ips = _resources(db, provider, "public_ip")
    pricing = Pricing(db, org_id, currency, [i.resource_id for i in ips])
    out = []
    for ip in ips:
        sku = str((ip.attributes or {}).get("sku") or "standard").lower()
        list_usd = AZURE_IP.get(sku, 3.65) if provider == "azure" else GCP_IP
        priced = pricing.cost(ip.resource_id, list_usd)
        if priced is None or priced[0] < 1:
            continue
        name = shlex.quote(ip.name or "")
        command = (
            f"az network public-ip delete --ids {shlex.quote(ip.resource_id)}"
            if provider == "azure"
            else f"gcloud compute addresses delete {name} --region {shlex.quote(ip.region or '')} "
            f"--project {shlex.quote(ip.account_id or '')}"
        )
        out.append(FindingIn(
            key=ip.resource_id, title=f"Release unused public IP {ip.name or ip.resource_id}",
            description=f"A reserved public address used by nothing (cost from {priced[1]}).",
            savings_monthly=priced[0], currency=currency, risk="low", effort="low",
            **_common(ip, provider),
            steps=["Check no DNS record or allow-list still points at the address.",
                   "Release it (a released address cannot be got back)."],
            commands=[command], evidence={"address": (ip.attributes or {}).get("address"),
                                          "basis": priced[1]},
        ))  # fmt: skip
    return out


@detector("azure.disk_unattached", category="waste", title="Unattached Azure disks")
def azure_disks(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    return _disks(db, org_id, currency, "azure")


@detector("azure.ip_unused", category="waste", title="Unused Azure public IP addresses")
def azure_ips(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    return _ips(db, org_id, currency, "azure")


@detector("gcp.disk_unattached", category="waste", title="Unattached Google Cloud disks")
def gcp_disks(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    return _disks(db, org_id, currency, "gcp")


@detector("gcp.ip_unused", category="waste", title="Unused Google Cloud external addresses")
def gcp_ips(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    return _ips(db, org_id, currency, "gcp")
