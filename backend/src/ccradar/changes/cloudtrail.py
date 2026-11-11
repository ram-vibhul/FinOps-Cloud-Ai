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
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ccradar.changes.service import ChangeIn, record
from ccradar.clouds.aws.client import AwsAccessError, AwsGateway
from ccradar.db.base import utcnow
from ccradar.db.tenant import set_tenant
from ccradar.models import CloudConnection, CostDaily

log = logging.getLogger(__name__)
MAX_PAGES = 5
MAX_REGIONS = 6

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


def to_change(event: dict[str, Any], region: str) -> ChangeIn | None:
    name = event.get("EventName", "")
    if name not in COST_EVENTS:
        return None
    detail: dict[str, Any] = {}
    with contextlib.suppress(json.JSONDecodeError):
        detail = json.loads(event.get("CloudTrailEvent") or "{}")
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
        attributes={"event_source": source, "event_name": name},
    )


def import_connection(
    db: Session, gateway: AwsGateway, connection_id: Any, *, hours: int = 24
) -> int:
    set_tenant(db, bypass=True)
    conn = db.get(CloudConnection, connection_id)
    if conn is None or conn.status != "active" or not conn.role_arn:
        return 0
    org_id = conn.org_id
    regions = [
        r
        for r in db.scalars(
            select(CostDaily.region)
            .distinct()
            .where(
                CostDaily.org_id == org_id,
                CostDaily.sub_account_id == conn.account_id,
                CostDaily.region.is_not(None),
            )
        )
        if r
    ][:MAX_REGIONS] or ["us-east-1"]
    try:
        session = gateway.assume(conn.role_arn, conn.external_id, f"ccradar-{org_id}")
    except AwsAccessError:
        return 0
    start = utcnow() - timedelta(hours=hours)
    created = 0
    set_tenant(db, org_id=org_id)
    for region in regions:
        trail = gateway.client(session, "cloudtrail", region=region)
        token = None
        for _ in range(MAX_PAGES):
            args: dict[str, Any] = {
                "StartTime": start,
                "EndTime": utcnow(),
                "LookupAttributes": [{"AttributeKey": "ReadOnly", "AttributeValue": "false"}],
                "MaxResults": 50,
            }
            if token:
                args["NextToken"] = token
            try:
                page = trail.lookup_events(**args)
            except Exception as exc:  # throttling or a disabled region
                log.warning("CloudTrail lookup failed in %s: %s", region, type(exc).__name__)
                break
            for raw in page.get("Events", []):
                change = to_change(raw, region)
                if change is not None and record(db, org_id, change, source="cloudtrail")[1]:
                    created += 1
            token = page.get("NextToken")
            if not token:
                break
    return created
