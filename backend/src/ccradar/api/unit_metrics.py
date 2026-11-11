"""Unit economics: define business metrics, send their daily values, see cost per unit."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from ccradar.accounts.roles import Permission
from ccradar.api.deps import SessionDep, require
from ccradar.auth.web import AuthContext
from ccradar.core.errors import AppError, NotFoundError
from ccradar.costs import unit_economics
from ccradar.costs.explorer import check_dimension
from ccradar.db.base import utcnow
from ccradar.models import UnitMetric

router = APIRouter(prefix="/api/v1/unit-metrics", tags=["unit economics"])
Reader = Annotated[AuthContext, require(Permission.READ)]
Writer = Annotated[AuthContext, require(Permission.WRITE)]
MAX_VALUES = 5000


class MetricIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    unit: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=1000)
    filters: dict[str, list[str]] = Field(default_factory=dict)
    metric: Literal["effective", "billed"] = "effective"
    target_per_unit: float | None = Field(default=None, ge=0)

    @field_validator("filters")
    @classmethod
    def _filters(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        for dim in value:
            check_dimension(dim)
        return value


class Point(BaseModel):
    day: date
    value: float = Field(ge=0, le=1e15)


def _org(auth: AuthContext) -> uuid.UUID:
    assert auth.org_id is not None  # noqa: S101
    return auth.org_id


def _get(db: SessionDep, metric_id: uuid.UUID) -> UnitMetric:
    m = db.get(UnitMetric, metric_id)
    if m is None:
        raise NotFoundError("Metric not found.")
    return m


def _out(m: UnitMetric) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "name": m.name,
        "unit": m.unit,
        "description": m.description,
        "filters": m.filters,
        "metric": m.metric,
        "target_per_unit": m.target_per_unit,
    }


@router.get("")
def list_metrics(auth: Reader, db: SessionDep) -> list[dict[str, Any]]:
    return [_out(m) for m in db.scalars(select(UnitMetric).order_by(UnitMetric.name))]


@router.post("", status_code=201)
def create_metric(body: MetricIn, auth: Writer, db: SessionDep) -> dict[str, Any]:
    m = UnitMetric(org_id=auth.org_id, **body.model_dump())
    db.add(m)
    db.flush()
    return _out(m)


@router.put("/{metric_id}")
def update_metric(
    metric_id: uuid.UUID, body: MetricIn, auth: Writer, db: SessionDep
) -> dict[str, Any]:
    m = _get(db, metric_id)
    for field, value in body.model_dump().items():
        setattr(m, field, value)
    db.flush()
    return _out(m)


@router.delete("/{metric_id}", status_code=204)
def delete_metric(metric_id: uuid.UUID, auth: Writer, db: SessionDep) -> Response:
    db.delete(_get(db, metric_id))
    db.flush()
    return Response(status_code=204)


@router.post("/{metric_id}/values")
def add_values(
    metric_id: uuid.UUID, points: list[Point], auth: Writer, db: SessionDep
) -> dict[str, int]:
    """Daily values (a day sent again replaces its value). Up to 5,000 per call."""
    if len(points) > MAX_VALUES:
        raise AppError(f"Send at most {MAX_VALUES} values per call.", code="too_many_values")
    return {
        "stored": unit_economics.upsert_values(
            db, _org(auth), _get(db, metric_id), [(p.day, p.value) for p in points]
        )
    }


@router.post("/{metric_id}/values.csv")
async def upload_values(
    metric_id: uuid.UUID, auth: Writer, db: SessionDep, request: Request
) -> dict[str, int]:
    """The request body is a CSV with columns day,value (header optional), 2 MB at most."""
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > 2_000_000:
            raise AppError("The file is too large (2 MB at most).", code="file_too_large")
    raw = body.decode("utf-8-sig", "replace")
    points: list[tuple[date, float]] = []
    for i, row in enumerate(csv.reader(io.StringIO(raw))):
        if not row or (i == 0 and not row[0][:1].isdigit()):
            continue
        try:
            points.append((date.fromisoformat(row[0].strip()), float(row[1])))
        except (ValueError, IndexError) as error:
            raise AppError(
                f"Line {i + 1}: expected day,value (YYYY-MM-DD,number).", code="bad_csv"
            ) from error
    if len(points) > MAX_VALUES:
        raise AppError(f"At most {MAX_VALUES} rows.", code="too_many_values")
    return {"stored": unit_economics.upsert_values(db, _org(auth), _get(db, metric_id), points)}


@router.get("/{metric_id}/report")
def metric_report(
    metric_id: uuid.UUID,
    auth: Reader,
    db: SessionDep,
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
) -> dict[str, Any]:
    end = end or utcnow().date()
    start = start or end - timedelta(days=89)
    if start > end or (end - start).days > 400:
        raise AppError("Choose a range of up to 400 days.", code="invalid_range")
    return unit_economics.report(db, _org(auth), _get(db, metric_id), start, end)
