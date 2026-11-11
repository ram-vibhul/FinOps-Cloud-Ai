"""Store collected resources and answer inventory questions."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ccradar.clouds.aws.client import AwsAccessError, AwsGateway
from ccradar.costs.explorer import tag_aliases
from ccradar.costs.fx import converted
from ccradar.db.base import Base, utcnow
from ccradar.db.tenant import set_tenant
from ccradar.inventory.aws import COLLECTOR_TYPES, COLLECTORS, Found
from ccradar.models import CloudConnection, CloudResource, CostDaily, Organization

log = logging.getLogger(__name__)
MAX_REGIONS = 10


def owner_of(tags: dict[str, str], org: Organization | None) -> str | None:
    dims = ((org.settings if org else None) or {}).get("dimensions", {})
    for key in (dims.get("owner_tag", "owner"), "Owner", tag_aliases(org)["team"]):
        if tags.get(key):
            return tags[key]
    return None


def upsert(
    db: Session,
    org_id: uuid.UUID,
    account_id: str | None,
    items: list[Found],
    *,
    provider: str = "aws",
    seen_at: datetime | None = None,
) -> int:
    org = db.get(Organization, org_id)
    now = seen_at or utcnow()
    existing = (
        {
            r.resource_id: r
            for r in db.scalars(
                select(CloudResource).where(
                    CloudResource.provider == provider,
                    CloudResource.resource_id.in_([f.resource_id for f in items]),
                )
            )
        }
        if items
        else {}
    )
    for f in items:
        row = existing.get(f.resource_id)
        if row is None:
            row = CloudResource(
                org_id=org_id,
                provider=provider,
                resource_id=f.resource_id,
                first_seen_at=now,
                service=f.service,
                resource_type=f.resource_type,
            )
            db.add(row)
        row.account_id, row.region, row.service = account_id, f.region, f.service
        row.resource_type, row.name, row.state = f.resource_type, f.name, f.state
        row.created_in_provider_at, row.tags, row.attributes = f.created_at, f.tags, f.attributes
        row.owner, row.last_seen_at = owner_of(f.tags, org), now
    db.flush()
    return len(items)


def mark_gone(
    db: Session,
    provider: str,
    account_id: str | None,
    region: str,
    seen_before: datetime,
    types: set[str] | None = None,
) -> int:
    """Resources not seen in this collection are marked deleted (kept for history).
    ``types`` limits this to the resource types whose collection succeeded."""
    q = select(CloudResource).where(
        CloudResource.provider == provider,
        CloudResource.account_id == account_id,
        CloudResource.region == region,
        CloudResource.last_seen_at < seen_before,
        CloudResource.state != "deleted",
    )
    if types is not None:
        q = q.where(CloudResource.resource_type.in_(types))
    rows = db.scalars(q).all()
    for r in rows:
        r.state = "deleted"
    db.flush()
    return len(rows)


def billed_regions(db: Session, org_id: uuid.UUID, account: str | None) -> list[str]:
    """Regions with spend for this account (at most MAX_REGIONS), always with us-east-1."""
    regions = [
        r
        for r in db.scalars(
            select(CostDaily.region)
            .distinct()
            .where(
                CostDaily.org_id == org_id,
                CostDaily.sub_account_id == account,
                CostDaily.region.is_not(None),
            )
        )
        if r and r != "global"
    ][:MAX_REGIONS]
    if "us-east-1" not in regions:
        regions.append("us-east-1")  # global services (CloudFront, Route 53) are listed there
    return regions


def collect_aws(db: Session, gateway: AwsGateway, connection_id: Any) -> dict[str, int]:
    set_tenant(db, bypass=True)
    conn = db.get(CloudConnection, connection_id)
    if conn is None or conn.status != "active" or not conn.role_arn:
        return {}
    org_id, account = conn.org_id, conn.account_id
    regions = billed_regions(db, org_id, account)
    try:
        session = gateway.assume(conn.role_arn, conn.external_id, f"ccradar-{org_id}")
    except AwsAccessError:
        return {}
    set_tenant(db, org_id=org_id)
    started = utcnow()
    counts: dict[str, int] = {}
    for region in regions:
        complete: set[str] = set()  # resource types collected in full
        for service, collector in COLLECTORS:
            try:
                client = gateway.client(session, service, region=region)
                items = list(collector(client, region))
            except Exception as error:  # missing permission or a disabled region: keep going
                log.info(
                    "inventory %s skipped in %s: %s",
                    collector.__name__,
                    region,
                    type(error).__name__,
                )
                continue
            upsert(db, org_id, account, items, seen_at=started)
            complete.update(COLLECTOR_TYPES[collector.__name__])
            counts[collector.__name__] = counts.get(collector.__name__, 0) + len(items)
        # A failed collector never marks its resources deleted; the others still can.
        mark_gone(db, "aws", account, region, started, types=complete)
        if region == "us-east-1":
            mark_gone(db, "aws", account, "global", started, types=complete)
        counts["metrics"] = counts.get("metrics", 0) + _collect_metrics(
            db, gateway, session, org_id, account, region
        )
    return counts


def _collect_metrics(
    db: Session,
    gateway: AwsGateway,
    session: Any,
    org_id: uuid.UUID,
    account: str | None,
    region: str,
) -> int:
    """CloudWatch usage for right-sizing; a missing permission only skips this part."""
    from ccradar.clouds.aws import metrics

    resources = metrics.running(db, account, region)
    if not resources:
        return 0
    try:
        client = gateway.client(session, "cloudwatch", region=region)
        return metrics.collect(db, client, org_id, resources)
    except Exception:  # no cloudwatch:GetMetricData, throttled, ...: keep the inventory
        return 0


def resource_costs(
    db: Session,
    org_id: uuid.UUID,
    resource_ids: list[str],
    *,
    days: int = 30,
    end: date | None = None,
    currency: str | None = None,
) -> dict[str, float]:
    """Cost per resource over ``days``, converted to ``currency`` (organisation default)."""
    if not resource_ids:
        return {}
    table = Base.metadata.tables["cost_line_items"]
    if currency is None:
        org = db.get(Organization, org_id)
        currency = org.default_currency if org else "USD"
    amount = converted(
        table.c.effective_cost,
        table.c.billing_currency,
        table.c.charge_date,
        currency,
        table.c.org_id,
    )
    last: date = (
        end
        or db.scalar(select(func.max(table.c.charge_date)).where(table.c.org_id == org_id))
        or utcnow().date()
    )
    rows = db.execute(
        select(table.c.resource_id, func.sum(amount))
        .where(
            table.c.org_id == org_id,
            table.c.resource_id.in_(resource_ids),
            table.c.charge_date > last - timedelta(days=days),
            table.c.charge_date <= last,
        )
        .group_by(table.c.resource_id)
    ).all()
    return {r: round(float(v), 2) for r, v in rows if v is not None}


def age_days(row: CloudResource, now: datetime | None = None) -> int | None:
    started = row.created_in_provider_at or row.first_seen_at
    if started is None:
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return max(0, ((now or utcnow()) - started).days)
