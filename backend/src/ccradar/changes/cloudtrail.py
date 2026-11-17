"""Infrastructure changes from AWS CloudTrail (read-only ``LookupEvents``).

Only write events that usually change cost are kept (instances, databases, load
balancers, NAT gateways, volumes, clusters, scaling). ``LookupEvents`` is regional and
limited to about 2 requests per second, so we query the regions where the account
actually has spend, for the last day, and stop after a few pages.
"""

from __future__ import annotations

import contextlib
import json
import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ccradar.changes import radar
from ccradar.changes.service import ChangeIn, record
from ccradar.clouds.aws.client import AwsAccessError, AwsGateway
from ccradar.costs.policy_code import live_violations
from ccradar.db.base import utcnow
from ccradar.db.tenant import set_tenant
from ccradar.models import CloudConnection, CostDaily

log = logging.getLogger(__name__)
MAX_PAGES = 40  # 2,000 write events per region per scan
MAX_REGIONS = 10
OVERLAP = timedelta(minutes=10)  # CloudTrail can deliver an event a few minutes late
MAX_LOOKBACK = timedelta(hours=24)

COST_EVENTS = {
    "RunInstances",
    "TerminateInstances",
    "ModifyInstanceAttribute",
    "StartInstances",
    "StopInstances",
    "CreateVolume",
    "ModifyVolume",
    "DeleteVolume",
    "CreateNatGateway",
    "DeleteNatGateway",
    "AllocateAddress",
    "ReleaseAddress",
    "CreateDBInstance",
    "ModifyDBInstance",
    "DeleteDBInstance",
    "CreateDBCluster",
    "ModifyDBCluster",
    "CreateLoadBalancer",
    "DeleteLoadBalancer",
    "UpdateAutoScalingGroup",
    "CreateAutoScalingGroup",
    "PutScalingPolicy",
    "CreateCluster",
    "ModifyCluster",
    "ResizeCluster",
    "DeleteCluster",
    "PauseCluster",
    "ResumeCluster",
    "SetDesiredCapacity",
    "CreateNodegroup",
    "UpdateNodegroupConfig",
    "CreateCacheCluster",
    "ModifyCacheCluster",
    "CreateReplicationGroup",
    "ModifyReplicationGroup",
    "UpdateDomainConfig",
    "CreateFunction",
    "PutProvisionedConcurrencyConfig",
    "CreateEndpoint",
    "UpdateEndpoint",
    "PutBucketLifecycleConfiguration",
}

SERVICES = {
    "ec2.amazonaws.com": "Amazon Elastic Compute Cloud",
    "rds.amazonaws.com": "Amazon Relational Database Service",
    "elasticloadbalancing.amazonaws.com": "Elastic Load Balancing",
    "autoscaling.amazonaws.com": "Amazon Elastic Compute Cloud",
    "redshift.amazonaws.com": "Amazon Redshift",
    "eks.amazonaws.com": "Amazon Elastic Kubernetes Service",
    "elasticache.amazonaws.com": "Amazon ElastiCache",
    "es.amazonaws.com": "Amazon OpenSearch Service",
    "lambda.amazonaws.com": "AWS Lambda",
    "sagemaker.amazonaws.com": "Amazon SageMaker",
    "s3.amazonaws.com": "Amazon Simple Storage Service",
}


def parse_detail(event: dict[str, Any]) -> dict[str, Any]:
    """The full CloudTrail record (request and response) inside a LookupEvents item."""
    detail: dict[str, Any] = {}
    with contextlib.suppress(json.JSONDecodeError, TypeError):
        detail = json.loads(event.get("CloudTrailEvent") or "{}")
    return detail if isinstance(detail, dict) else {}


def to_change(
    event: dict[str, Any], region: str, impact: dict[str, Any] | None = None
) -> ChangeIn | None:
    name = event.get("EventName", "")
    if name not in COST_EVENTS:
        return None
    detail = parse_detail(event)
    source = event.get("EventSource") or detail.get("eventSource", "")
    resources = event.get("Resources") or []
    resource = resources[0].get("ResourceName") if resources else None
    actor = event.get("Username") or (detail.get("userIdentity") or {}).get("arn")
    account = detail.get("recipientAccountId") or (detail.get("userIdentity") or {}).get(
        "accountId"
    )
    return ChangeIn(
        occurred_at=event.get("EventTime"),
        kind="infrastructure",
        title=f"{name}{' ' + resource if resource else ''}"[:300],
        service=SERVICES.get(source),
        account=account,
        region=region,
        resource_id=resource,
        actor=(actor or None) and str(actor)[:320],
        external_id=event.get("EventId"),
        attributes={
            "event_source": source,
            "event_name": name,
            **({"impact": impact} if impact else {}),  # Live Radar (10.1)
        },
    )


def _regions(db: Session, org_id: Any, account: str | None) -> tuple[list[str], bool]:
    """Regions where the account has spend, busiest first; and whether some were left out."""
    rows = db.execute(
        select(CostDaily.region, func.sum(CostDaily.effective_cost))
        .where(
            CostDaily.org_id == org_id,
            CostDaily.sub_account_id == account,
            CostDaily.region.is_not(None),
        )
        .group_by(CostDaily.region)
        .order_by(func.sum(CostDaily.effective_cost).desc())
    ).all()
    regions = [r for r, _ in rows if r]
    return (regions[:MAX_REGIONS] or ["us-east-1"]), len(regions) > MAX_REGIONS


def _scan_region(
    db: Session,
    trail: Any,
    region: str,
    start: datetime,
    end: datetime,
    org_id: Any,
    known: radar.Known,
    pricing: radar.Pricing,
    fresh: list[Any],
) -> tuple[int, str | None]:
    """Read one region's write events; new changes are appended to ``fresh``.
    Returns (events read, problem or None)."""
    read, token = 0, None
    for _ in range(MAX_PAGES):
        args: dict[str, Any] = {
            "StartTime": start,
            "EndTime": end,
            "LookupAttributes": [{"AttributeKey": "ReadOnly", "AttributeValue": "false"}],
            "MaxResults": 50,
        }
        if token:
            args["NextToken"] = token
        try:
            page = trail.lookup_events(**args)
        except Exception as exc:
            log.warning("CloudTrail lookup failed in %s: %s", region, type(exc).__name__)
            return read, f"{region}: {type(exc).__name__}"
        for raw in page.get("Events", []):
            read += 1
            if raw.get("EventName") not in COST_EVENTS:
                continue
            detail = {"eventSource": raw.get("EventSource"), **parse_detail(raw)}
            detail.setdefault("eventName", raw.get("EventName"))
            detail.setdefault("awsRegion", region)
            impact = radar.assess(detail, known, pricing)
            change = to_change(raw, region, impact.to_dict() if impact else None)
            if change is None:
                continue
            if not detail.get("errorCode"):  # 11.41: policies, the moment it happens
                hits = live_violations(
                    db,
                    str(detail.get("eventName")),
                    detail.get("awsRegion"),
                    detail.get("requestParameters") or {},
                )
                if hits:
                    change.attributes = {**change.attributes, "policy": hits}
            event, is_new = record(db, org_id, change, source="cloudtrail")
            if is_new:
                fresh.append(event)
        token = page.get("NextToken")
        if not token:
            return read, None
    return read, f"{region}: more than {MAX_PAGES * 50} changes"


def import_connection(
    db: Session, gateway: AwsGateway, connection_id: Any, *, hours: int = 24
) -> int:
    """Read the account's write events since the last complete scan (11.4), price them,
    record them and alert on costly ones. A scan cut short (too many events, throttling,
    more regions than we read) is marked incomplete and the next scan starts again from
    the same point, so nothing is skipped silently."""
    set_tenant(db, bypass=True)
    conn = db.get(CloudConnection, connection_id)
    if conn is None or conn.status != "active" or not conn.role_arn:
        return 0
    org_id = conn.org_id
    regions, regions_left_out = _regions(db, org_id, conn.account_id)
    try:
        session = gateway.assume(conn.role_arn, conn.external_id, f"ccradar-{org_id}")
    except AwsAccessError:
        return 0
    now = utcnow()
    floor = now - min(timedelta(hours=hours), MAX_LOOKBACK)
    previous = ((conn.settings or {}).get("radar_scan") or {}).get("through")
    # Where the last complete scan ended; the overlap catches events delivered late.
    resume = max(floor, datetime.fromisoformat(previous)) if previous else floor
    start = max(floor, resume - OVERLAP)
    read, problems = 0, []
    set_tenant(db, org_id=org_id)
    pricing, known = radar.pricing_for(db, org_id), radar.inventory_lookup(db)
    fresh: list[Any] = []
    for region in regions:
        region_read, problem = _scan_region(
            db, gateway.client(session, "cloudtrail", region=region), region, start, now,
            org_id, known, pricing, fresh,
        )  # fmt: skip
        read += region_read
        if problem:
            problems.append(problem)
    created = len(fresh)
    if regions_left_out:
        problems.append(f"only the {MAX_REGIONS} busiest regions are read")
    complete = not problems
    set_tenant(db, bypass=True)
    conn.settings = {
        **(conn.settings or {}),
        "radar_scan": {
            "at": now.isoformat(),
            # Where the next scan resumes: now if this one read everything, else the same
            # point again, so a busy account never skips events (re-reads are de-duplicated).
            "through": (now if complete else resume).isoformat(),
            "complete": complete,
            "problems": problems[:10],
            "events_read": read,
            "regions": regions,
        },
    }
    set_tenant(db, org_id=org_id)
    radar.alert(db, org_id, fresh)  # each change is alerted once, when first seen
    return created
