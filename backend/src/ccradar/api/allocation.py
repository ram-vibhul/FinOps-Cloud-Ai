"""Shared-cost allocation rules, showback and chargeback (JSON and CSV)."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from ccradar.accounts.roles import Permission
from ccradar.api.deps import SessionDep, require
from ccradar.auth.web import AuthContext
from ccradar.core.errors import NotFoundError
from ccradar.costs.allocation import check_rule, showback
from ccradar.costs.explorer import check_dimension
from ccradar.db.base import utcnow
from ccradar.models import AllocationRule

router = APIRouter(prefix="/api/v1/allocation", tags=["allocation"])
Reader = Annotated[AuthContext, require(Permission.READ)]
Admin = Annotated[AuthContext, require(Permission.MANAGE_SETTINGS)]
MONTH = r"^\d{4}-(0[1-9]|1[0-2])$"


class RuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    position: int = Field(default=100, ge=1, le=10000)
    dimension: str = Field(min_length=1, max_length=140)
    filters: dict[str, list[str]] = Field(min_length=1)
    method: Literal["proportional", "even", "fixed"] = "proportional"
    targets: list[str] = Field(default_factory=list, max_length=200)
    percents: dict[str, float] = Field(default_factory=dict)

    @field_validator("dimension")
    @classmethod
    def _dimension(cls, value: str) -> str:
        check_dimension(value)
        return value

    @field_validator("filters")
    @classmethod
    def _filters(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        for dim, values in value.items():
            check_dimension(dim)
            if not values:
                raise ValueError(f"{dim}: give at least one value")
        return value


def _org(auth: AuthContext) -> uuid.UUID:
    assert auth.org_id is not None  # noqa: S101
    return auth.org_id


def _out(r: AllocationRule) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "name": r.name,
        "position": r.position,
        "dimension": r.dimension,
        "filters": r.filters,
        "method": r.method,
        "targets": r.targets,
        "percents": r.percents,
    }


@router.get("/rules")
def list_rules(auth: Reader, db: SessionDep) -> list[dict[str, Any]]:
    return [
        _out(r)
        for r in db.scalars(
            select(AllocationRule).order_by(AllocationRule.position, AllocationRule.name)
        )
    ]


@router.post("/rules", status_code=201)
def create_rule(body: RuleIn, auth: Admin, db: SessionDep) -> dict[str, Any]:
    check_rule(body.method, body.targets, body.percents)
    r = AllocationRule(org_id=auth.org_id, **body.model_dump())
    db.add(r)
    db.flush()
    return _out(r)


@router.put("/rules/{rule_id}")
def update_rule(rule_id: uuid.UUID, body: RuleIn, auth: Admin, db: SessionDep) -> dict[str, Any]:
    r = db.get(AllocationRule, rule_id)
    if r is None:
        raise NotFoundError("Rule not found.")
    check_rule(body.method, body.targets, body.percents)
    for field, value in body.model_dump().items():
        setattr(r, field, value)
    db.flush()
    return _out(r)


@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(rule_id: uuid.UUID, auth: Admin, db: SessionDep) -> Response:
    r = db.get(AllocationRule, rule_id)
    if r is None:
        raise NotFoundError("Rule not found.")
    db.delete(r)
    db.flush()
    return Response(status_code=204)


def _month(value: str | None) -> date:
    if value:
        y, m = value.split("-")
        return date(int(y), int(m), 1)
    return utcnow().date().replace(day=1)


@router.get("/showback")
def get_showback(
    auth: Reader,
    db: SessionDep,
    dimension: Annotated[str, Query(min_length=1, max_length=140)],
    month: Annotated[str | None, Query(pattern=MONTH)] = None,
    metric: Literal["effective", "billed"] = "effective",
) -> dict[str, Any]:
    check_dimension(dimension)
    return showback(db, _org(auth), _month(month), dimension, metric)


@router.get("/showback.csv")
def showback_csv(
    auth: Reader,
    db: SessionDep,
    dimension: Annotated[str, Query(min_length=1, max_length=140)],
    month: Annotated[str | None, Query(pattern=MONTH)] = None,
    metric: Literal["effective", "billed"] = "effective",
) -> Response:
    check_dimension(dimension)
    data = showback(db, _org(auth), _month(month), dimension, metric)
    rules = [r["name"] for r in data["rules"]]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([dimension, "direct", *[f"shared: {n}" for n in rules], "total", "currency"])
    for row in data["rows"]:
        w.writerow(
            [
                row["value"],
                f"{row['direct']:.2f}",
                *[f"{row['shared'].get(n, 0):.2f}" for n in rules],
                f"{row['total']:.2f}",
                data["currency"],
            ]
        )
    w.writerow(
        ["TOTAL", "", *["" for _ in rules], f"{data['allocated_total']:.2f}", data["currency"]]
    )
    return Response(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="chargeback-{data["month"]}.csv"'},
    )
