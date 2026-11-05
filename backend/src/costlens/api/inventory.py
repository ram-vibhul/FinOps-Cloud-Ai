"""Resource inventory and hourly costs."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select

from costlens.accounts.roles import Permission
from costlens.api.deps import SessionDep, require
from costlens.auth.web import AuthContext
from costlens.core.errors import NotFoundError
from costlens.costs.fx import converted
from costlens.db.base import Base, utcnow
from costlens.inventory.service import age_days, resource_costs
from costlens.models import CloudResource, Organization

router = APIRouter(tags=["inventory"])
Reader = Annotated[AuthContext, require(Permission.READ)]


class ResourceOut(BaseModel):
    id: uuid.UUID
    provider: str
    account_id: str | None
    region: str | None
    service: str
    resource_type: str
    resource_id: str
    name: str | None
    state: str | None
    owner: str | None
    age_days: int | None = None
    cost_30d: float | None = None
    cost_source: str | None = None
    tags: dict[str, str]
    attributes: dict[str, Any]
    last_seen_at: datetime


def _out(row: CloudResource, costs: dict[str, float]) -> ResourceOut:
    cost, source = (
        costs.get(row.resource_id),
        "billing export" if row.resource_id in costs else None,
    )
    if cost is None and (row.attributes or {}).get("cost_14d_ce") is not None:
        cost, source = float(row.attributes["cost_14d_ce"]) * 30 / 14, "Cost Explorer (estimated)"
    out = ResourceOut.model_validate(row, from_attributes=True)
    out.age_days, out.cost_30d, out.cost_source = age_days(row), cost, source
    return out


@router.get("/api/v1/resources")
def list_resources(
    auth: Reader,
    db: SessionDep,
    resource_type: str | None = None,
    state: str | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    include_deleted: bool = False,
    sort: Annotated[str, Query(pattern="^(cost|age|name)$")] = "cost",
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    assert auth.org_id is not None  # noqa: S101
    stmt = select(CloudResource)
    if resource_type:
        stmt = stmt.where(CloudResource.resource_type == resource_type)
    if state:
        stmt = stmt.where(CloudResource.state == state)
    if not include_deleted:
        stmt = stmt.where(or_(CloudResource.state.is_(None), CloudResource.state != "deleted"))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                CloudResource.name.ilike(like),
                CloudResource.resource_id.ilike(like),
                CloudResource.owner.ilike(like),
            )
        )
    rows = db.scalars(stmt.limit(5000)).all()
    costs = resource_costs(db, auth.org_id, [r.resource_id for r in rows])
    items = [_out(r, costs) for r in rows]
    if sort == "cost":
        items.sort(key=lambda i: -(i.cost_30d or 0))
    elif sort == "age":
        items.sort(key=lambda i: -(i.age_days or 0))
    else:
        items.sort(key=lambda i: (i.name or i.resource_id).lower())
    counts = dict(
        db.execute(
            select(CloudResource.resource_type, func.count())
            .where(or_(CloudResource.state.is_(None), CloudResource.state != "deleted"))
            .group_by(CloudResource.resource_type)
        ).all()
    )
    org = db.get(Organization, auth.org_id)
    return {
        "currency": org.default_currency if org else "USD",
        "total": len(items),
        "by_type": counts,
        "items": [i.model_dump(mode="json") for i in items[:limit]],
    }


@router.get("/api/v1/resources/{resource_uuid}")
def get_resource(resource_uuid: uuid.UUID, auth: Reader, db: SessionDep) -> dict[str, Any]:
    assert auth.org_id is not None  # noqa: S101
    row = db.get(CloudResource, resource_uuid)
    if row is None:
        raise NotFoundError("Resource not found.")
    table = Base.metadata.tables["cost_line_items"]
    org = db.get(Organization, auth.org_id)
    currency = org.default_currency if org else "USD"
    amount = converted(
        table.c.effective_cost,
        table.c.billing_currency,
        table.c.charge_date,
        currency,
        table.c.org_id,
    )
    daily = db.execute(
        select(table.c.charge_date, func.sum(amount))
        .where(table.c.org_id == auth.org_id, table.c.resource_id == row.resource_id)
        .group_by(table.c.charge_date)
        .order_by(table.c.charge_date)
    ).all()
    costs = resource_costs(db, auth.org_id, [row.resource_id])
    return {
        "resource": _out(row, costs).model_dump(mode="json"),
        "currency": currency,
        "daily": [{"day": d.isoformat(), "cost": round(float(v or 0), 2)} for d, v in daily[-90:]],
    }


@router.get("/api/v1/costs/hourly")
def hourly(
    auth: Reader, db: SessionDep, hours: Annotated[int, Query(ge=6, le=336)] = 72
) -> dict[str, Any]:
    """Cost per hour for the most recent hours with data (top services + Other)."""
    assert auth.org_id is not None  # noqa: S101
    table = Base.metadata.tables["cost_hourly"]
    org = db.get(Organization, auth.org_id)
    currency = org.default_currency if org else "USD"
    latest = db.scalar(select(func.max(table.c.hour)).where(table.c.org_id == auth.org_id))
    if latest is None:
        return {"currency": currency, "hours": [], "series": []}
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=UTC)
    start = latest - timedelta(hours=hours - 1)
    day = func.date(table.c.hour)
    amount = converted(
        table.c.effective_cost, table.c.billing_currency, day, currency, table.c.org_id
    )
    rows = db.execute(
        select(table.c.hour, table.c.service_name, func.sum(amount))
        .where(table.c.org_id == auth.org_id, table.c.hour >= start)
        .group_by(table.c.hour, table.c.service_name)
    ).all()
    labels = [(start + timedelta(hours=i)) for i in range(hours)]
    index = {h.isoformat(): i for i, h in enumerate(labels)}
    totals: dict[str, float] = {}
    cells: dict[str, list[float]] = {}
    for raw_hour, service, value in rows:
        h = raw_hour if raw_hour.tzinfo else raw_hour.replace(tzinfo=UTC)
        i = index.get(h.isoformat())
        if i is None or value is None:
            continue
        cells.setdefault(service, [0.0] * hours)[i] += float(value)
        totals[service] = totals.get(service, 0.0) + float(value)
    ranked = sorted(totals, key=lambda k: -totals[k])
    series = [
        {
            "key": k,
            "label": k,
            "total": round(totals[k], 2),
            "values": [round(v, 4) for v in cells[k]],
        }
        for k in ranked[:7]
    ]
    if len(ranked) > 7:
        rest = ranked[7:]
        series.append(
            {
                "key": "Other",
                "label": f"Other ({len(rest)})",
                "total": round(sum(totals[k] for k in rest), 2),
                "values": [round(sum(cells[k][i] for k in rest), 4) for i in range(hours)],
            }
        )
    return {
        "currency": currency,
        "hours": [h.isoformat() for h in labels],
        "series": series,
        "generated_at": utcnow().isoformat(),
    }
