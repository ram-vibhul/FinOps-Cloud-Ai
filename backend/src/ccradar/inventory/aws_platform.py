"""Networking, observability, security and end-user computing inventory (read-only):
CloudFront, Route 53, VPN, Transit Gateway attachments, Direct Connect, CloudWatch
log groups / dashboards / alarms, KMS keys, Secrets Manager (metadata only, never values),
WAF web ACLs, CloudTrail trails and WorkSpaces."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ccradar.inventory.aws import Found, _pages, _tags

MAX_CHILDREN = 1000
GLOBAL_REGION = "us-east-1"  # CloudFront, Route 53 and CloudFront-scope WAF live here


def cloudfront_distributions(client: Any, region: str) -> Iterator[Found]:
    if region != GLOBAL_REGION:
        return
    for page in _pages(client.list_distributions, "Marker", "NextMarker", MaxItems="100"):
        dl = page.get("DistributionList") or page
        for d in (dl.get("Items") or [])[:MAX_CHILDREN]:
            yield Found(
                "Amazon CloudFront",
                "cloudfront_distribution",
                d.get("ARN") or d["Id"],
                "global",
                name=d.get("DomainName"),
                state="enabled" if d.get("Enabled") else "disabled",
                attributes={"price_class": d.get("PriceClass"), "id": d["Id"]},
            )
        if not dl.get("IsTruncated"):
            return


def route53_zones(client: Any, region: str) -> Iterator[Found]:
    if region != GLOBAL_REGION:
        return
    for page in _pages(client.list_hosted_zones, "Marker", "NextMarker", MaxItems="100"):
        for z in page.get("HostedZones", [])[:MAX_CHILDREN]:
            yield Found(
                "Amazon Route 53",
                "route53_zone",
                z["Id"].rsplit("/", 1)[-1],
                "global",
                name=z.get("Name"),
                state="active",
                attributes={
                    "records": z.get("ResourceRecordSetCount"),
                    "private": bool((z.get("Config") or {}).get("PrivateZone")),
                },
            )


def network_links(client: Any, region: str) -> Iterator[Found]:
    """VPN connections and Transit Gateway attachments (EC2 API)."""
    for v in client.describe_vpn_connections().get("VpnConnections", [])[:MAX_CHILDREN]:
        tunnels = [t.get("Status") for t in v.get("VgwTelemetry") or []]
        yield Found(
            "Amazon Virtual Private Cloud",
            "vpn_connection",
            v["VpnConnectionId"],
            region,
            name=_tags(v.get("Tags")).get("Name") or v["VpnConnectionId"],
            state=v.get("State"),
            tags=_tags(v.get("Tags")),
            attributes={
                "tunnels_up": sum(1 for t in tunnels if t == "UP"),
                "tunnels": len(tunnels),
            },
        )
    for page in _pages(
        client.describe_transit_gateway_attachments, "NextToken", "NextToken", MaxResults=1000
    ):
        for a in page.get("TransitGatewayAttachments", [])[:MAX_CHILDREN]:
            yield Found(
                "Amazon Virtual Private Cloud",
                "tgw_attachment",
                a["TransitGatewayAttachmentId"],
                region,
                name=_tags(a.get("Tags")).get("Name") or a["TransitGatewayAttachmentId"],
                state=a.get("State"),
                tags=_tags(a.get("Tags")),
                attributes={"type": a.get("ResourceType"), "resource": a.get("ResourceId")},
            )


def direct_connect(client: Any, region: str) -> Iterator[Found]:
    for c in client.describe_connections().get("connections", [])[:MAX_CHILDREN]:
        yield Found(
            "AWS Direct Connect",
            "dx_connection",
            c["connectionId"],
            region,
            name=c.get("connectionName"),
            state=c.get("connectionState"),  # available | down | ...
            attributes={"bandwidth": c.get("bandwidth")},
        )


def log_groups(client: Any, region: str) -> Iterator[Found]:
    for page in _pages(client.describe_log_groups, "nextToken", "nextToken", limit=50):
        for g in page.get("logGroups", [])[:MAX_CHILDREN]:
            yield Found(
                "AmazonCloudWatch",
                "log_group",
                g.get("arn", "").removesuffix(":*") or g["logGroupName"],
                region,
                name=g["logGroupName"],
                state="active",
                created_at=_ms(g.get("creationTime")),
                attributes={
                    "retention_days": g.get("retentionInDays"),
                    "stored_bytes": g.get("storedBytes") or 0,
                    "log_class": g.get("logGroupClass") or "STANDARD",
                },
            )


def _ms(value: int | None) -> Any:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(value / 1000, UTC) if value else None


def cloudwatch_items(client: Any, region: str) -> Iterator[Found]:
    for page in _pages(client.list_dashboards, "NextToken", "NextToken"):
        for d in page.get("DashboardEntries", [])[:MAX_CHILDREN]:
            yield Found(
                "AmazonCloudWatch",
                "cw_dashboard",
                d.get("DashboardArn") or d["DashboardName"],
                region,
                name=d["DashboardName"],
                state="active",
                attributes={"last_modified": str(d.get("LastModified") or "")},
            )
    for page in _pages(client.describe_alarms, "NextToken", "NextToken", MaxRecords=100):
        for a in page.get("MetricAlarms", [])[:MAX_CHILDREN]:
            yield Found(
                "AmazonCloudWatch",
                "cw_alarm",
                a.get("AlarmArn") or a["AlarmName"],
                region,
                name=a["AlarmName"],
                state=str(a.get("StateValue") or "").lower() or None,
                attributes={
                    "state_since": str(a.get("StateUpdatedTimestamp") or ""),
                    "high_resolution": (a.get("Period") or 60) < 60,
                },
            )


def kms_keys(client: Any, region: str) -> Iterator[Found]:
    for page in _pages(client.list_keys, "Marker", "NextMarker", Limit=1000):
        for k in page.get("Keys", [])[:MAX_CHILDREN]:
            meta = client.describe_key(KeyId=k["KeyId"]).get("KeyMetadata") or {}
            if meta.get("KeyManager") != "CUSTOMER":
                continue  # AWS-managed keys are free
            yield Found(
                "AWS Key Management Service",
                "kms_key",
                meta.get("Arn") or k["KeyArn"],
                region,
                name=meta.get("Description") or k["KeyId"],
                state=str(meta.get("KeyState") or "").lower()
                or None,  # enabled | disabled | pendingdeletion
                created_at=meta.get("CreationDate"),
                attributes={"multi_region": bool(meta.get("MultiRegion"))},
            )


def secrets(client: Any, region: str) -> Iterator[Found]:
    for page in _pages(client.list_secrets, "NextToken", "NextToken", MaxResults=100):
        for s in page.get("SecretList", [])[:MAX_CHILDREN]:
            yield Found(
                "AWS Secrets Manager",
                "secret",
                s["ARN"],
                region,
                name=s.get("Name"),
                state="deleted" if s.get("DeletedDate") else "active",
                created_at=s.get("CreatedDate"),
                tags=_tags(s.get("Tags")),
                attributes={"last_accessed": str(s.get("LastAccessedDate") or "")},
            )


def waf_acls(client: Any, region: str) -> Iterator[Found]:
    scopes = ["REGIONAL"] + (["CLOUDFRONT"] if region == GLOBAL_REGION else [])
    for scope in scopes:
        for page in _pages(
            client.list_web_acls, "NextMarker", "NextMarker", Scope=scope, Limit=100
        ):
            for acl in page.get("WebACLs", [])[:MAX_CHILDREN]:
                used: int | None
                if scope == "REGIONAL":
                    used = len(
                        client.list_resources_for_web_acl(WebACLArn=acl["ARN"]).get(
                            "ResourceArns", []
                        )
                    )
                else:
                    used = None  # CloudFront associations are on the distribution
                yield Found(
                    "AWS WAF",
                    "waf_acl",
                    acl["ARN"],
                    region if scope == "REGIONAL" else "global",
                    name=acl.get("Name"),
                    state="active",
                    attributes={"scope": scope, "resources": used},
                )


def cloudtrail_trails(client: Any, region: str) -> Iterator[Found]:
    for t in client.describe_trails(includeShadowTrails=False).get("trailList", [])[:MAX_CHILDREN]:
        yield Found(
            "AWS CloudTrail",
            "cloudtrail_trail",
            t.get("TrailARN") or t["Name"],
            t.get("HomeRegion") or region,
            name=t.get("Name"),
            state="active",
            attributes={
                "multi_region": bool(t.get("IsMultiRegionTrail")),
                "organization": bool(t.get("IsOrganizationTrail")),
                "data_events": bool(t.get("HasCustomEventSelectors")),
            },
        )


def workspaces(client: Any, region: str) -> Iterator[Found]:
    status: dict[str, str] = {}
    for page in _pages(client.describe_workspaces_connection_status, "NextToken", "NextToken"):
        for s in page.get("WorkspacesConnectionStatus", []):
            status[s["WorkspaceId"]] = str(s.get("LastKnownUserConnectionTimestamp") or "")
    for page in _pages(client.describe_workspaces, "NextToken", "NextToken", Limit=25):
        for w in page.get("Workspaces", [])[:MAX_CHILDREN]:
            props = w.get("WorkspaceProperties") or {}
            yield Found(
                "Amazon WorkSpaces",
                "workspace",
                w["WorkspaceId"],
                region,
                name=w.get("UserName") or w["WorkspaceId"],
                state=str(w.get("State") or "").lower() or None,
                attributes={
                    "running_mode": props.get("RunningMode"),  # ALWAYS_ON | AUTO_STOP
                    "compute": props.get("ComputeTypeName"),
                    "last_connection": status.get(w["WorkspaceId"], ""),
                },
            )


PLATFORM_COLLECTORS = [
    ("cloudfront", cloudfront_distributions),
    ("route53", route53_zones),
    ("ec2", network_links),
    ("directconnect", direct_connect),
    ("logs", log_groups),
    ("cloudwatch", cloudwatch_items),
    ("kms", kms_keys),
    ("secretsmanager", secrets),
    ("wafv2", waf_acls),
    ("cloudtrail", cloudtrail_trails),
    ("workspaces", workspaces),
]
PLATFORM_TYPES = {
    "cloudfront_distributions": ("cloudfront_distribution",),
    "route53_zones": ("route53_zone",),
    "network_links": ("vpn_connection", "tgw_attachment"),
    "direct_connect": ("dx_connection",),
    "log_groups": ("log_group",),
    "cloudwatch_items": ("cw_dashboard", "cw_alarm"),
    "kms_keys": ("kms_key",),
    "secrets": ("secret",),
    "waf_acls": ("waf_acl",),
    "cloudtrail_trails": ("cloudtrail_trail",),
    "workspaces": ("workspace",),
}
