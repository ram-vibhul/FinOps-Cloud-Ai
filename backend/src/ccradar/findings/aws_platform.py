"""Platform detectors: CloudWatch log groups kept forever, VPN connections with no tunnel
up, and regional WAF web ACLs attached to nothing."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from ccradar.db.base import utcnow
from ccradar.findings.aws import MONTH, Pricing, _common, _resources
from ccradar.findings.aws_compute import _usd
from ccradar.findings.registry import FindingIn, detector
from ccradar.inventory.service import age_days

MIN_SAVING = 1.0
LOG_GB_MONTH_USD = 0.03  # stored (compressed) log data
LOG_MIN_GB = 10
LOG_KEEP_DAYS = 90
LOG_MIN_AGE_DAYS = 180
VPN_HOUR_USD = 0.05
WAF_ACL_MONTH_USD = 5.0


@detector("aws.log_retention", category="observability", title="Log groups that keep data forever")
def log_retention(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    usd = _usd(db, org_id, currency)
    if usd is None:
        return []
    now = utcnow()
    out = []
    for g in _resources(db, "log_group"):
        a = g.attributes or {}
        gb = float(a.get("stored_bytes") or 0) / 1e9
        age = age_days(g, now) or 0
        if a.get("retention_days") or gb < LOG_MIN_GB or age < LOG_MIN_AGE_DAYS:
            continue
        old_share = 1 - LOG_KEEP_DAYS / age  # assumes steady ingestion over the group's life
        saving = round(gb * old_share * LOG_GB_MONTH_USD * usd, 2)
        if saving < MIN_SAVING:
            continue
        out.append(
            FindingIn(
                key=g.resource_id,
                title=f"Set a retention period on log group {g.name}",
                description=(
                    f"It never expires and stores {gb:,.0f} GB after {age} days. Keeping "
                    f"{LOG_KEEP_DAYS} days would drop about {old_share:.0%} of it, assuming "
                    "steady logging (0.03 USD per GB-month, list price). Export to S3 first if "
                    "you must keep it."
                ),
                savings_monthly=saving,
                currency=currency,
                risk="low",
                effort="low",
                overlap=g.resource_id,
                **_common(g),
                steps=[
                    "Agree the retention your compliance rules need.",
                    "Set the retention (CloudWatch deletes older events within days).",
                ],
                commands=[
                    f"aws logs put-retention-policy --region {g.region} --log-group-name "
                    f"'{g.name}' --retention-in-days {LOG_KEEP_DAYS}"
                ],
                evidence={"stored_gb": round(gb, 1), "age_days": age},
            )
        )
    return out


@detector("aws.vpn_down", category="waste", title="VPN connections with no tunnel up")
def vpn_down(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    down = [
        v
        for v in _resources(db, "vpn_connection")
        if v.state == "available"
        and (v.attributes or {}).get("tunnels")
        and not (v.attributes or {}).get("tunnels_up")
    ]
    pricing = Pricing(db, org_id, currency, [v.resource_id for v in down])
    out = []
    for v in down:
        priced = pricing.cost(v.resource_id, VPN_HOUR_USD * 24 * MONTH)
        if priced is None or priced[0] < MIN_SAVING:
            continue
        out.append(
            FindingIn(
                key=v.resource_id,
                title=f"Remove VPN connection {v.name} (both tunnels down)",
                description=(
                    "Neither tunnel is up, so nothing uses it, but it is billed every hour. If "
                    "it is a standby link, check why its tunnels are down."
                ),
                savings_monthly=priced[0],
                currency=currency,
                risk="medium",
                effort="low",
                overlap=v.resource_id,
                **_common(v),
                steps=["Confirm with the network owner.", "Delete the VPN connection."],
                commands=[
                    f"aws ec2 delete-vpn-connection --region {v.region} --vpn-connection-id "
                    f"{v.resource_id}"
                ],
                evidence={"basis": priced[1]},
            )
        )
    return out


@detector("aws.waf_unused", category="waste", title="WAF web ACLs attached to nothing")
def waf_unused(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    usd = _usd(db, org_id, currency)
    if usd is None:
        return []
    out = []
    for acl in _resources(db, "waf_acl"):
        a = acl.attributes or {}
        if a.get("scope") != "REGIONAL" or a.get("resources") != 0:
            continue
        out.append(
            FindingIn(
                key=acl.resource_id,
                title=f"Delete unused WAF web ACL {acl.name}",
                description=(
                    "It protects no load balancer, API or app, but each web ACL costs 5 USD a "
                    "month plus 1 USD per rule (list price; the saving shows the ACL only)."
                ),
                savings_monthly=round(WAF_ACL_MONTH_USD * usd, 2),
                currency=currency,
                risk="low",
                effort="low",
                overlap=acl.resource_id,
                **_common(acl),
                steps=["Confirm it is not about to be attached.", "Delete the web ACL."],
                commands=[
                    f"aws wafv2 delete-web-acl --region {acl.region} --scope REGIONAL --name "
                    f"{acl.name} --id <id> --lock-token <token>"
                ],
                evidence={"scope": a.get("scope")},
            )
        )
    return out
