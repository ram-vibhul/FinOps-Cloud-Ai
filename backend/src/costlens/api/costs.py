"""Spend: summary with forecast, daily/monthly series, biggest movers."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query

from costlens.accounts.roles import Permission
from costlens.api.deps import SessionDep, require
from costlens.auth.web import AuthContext
from costlens.core.errors import AppError
from costlens.costs import analytics
from costlens.db.base import utcnow
from costlens.models import Organization

router = APIRouter(prefix="/api/v1/costs", tags=["costs"])
Reader = Annotated[AuthContext, require(Permission.READ)]
CurrencyQ = Annotated[str | None, Query(pattern=r"^[A-Z]{3}$")]
MetricQ = Annotated[Literal["effective", "billed"], Query()]
DimensionQ = Annotated[
    Literal[
        "provider", "service", "account", "region", "charge_category", "pricing_category", "none"
    ],
    Query(),
]


def _currency(db: SessionDep, auth: AuthContext, currency: str | None) -> str:
    if currency:
        return currency
    org = db.get(Organization, auth.org_id)
    return org.default_currency if org else "USD"


def _today() -> date:
    return utcnow().date()


@router.get("/summary")
def summary(
    auth: Reader, db: SessionDep, currency: CurrencyQ = None, metric: MetricQ = "effective"
) -> dict[str, Any]:
    assert auth.org_id is not None  # noqa: S101
    return analytics.summary(db, auth.org_id, _currency(db, auth, currency), metric, _today())


@router.get("/daily")
def daily(
    auth: Reader,
    db: SessionDep,
    currency: CurrencyQ = None,
    metric: MetricQ = "effective",
    dimension: DimensionQ = "service",
    start: date | None = None,
    end: date | None = None,
    top: Annotated[int, Query(ge=1, le=20)] = 7,
) -> dict[str, Any]:
    assert auth.org_id is not None  # noqa: S101
    end = end or analytics.latest_date(db, auth.org_id) or _today()
    start = start or end - timedelta(days=29)
    if start > end or (end - start).days > 400:
        raise AppError("Choose a range of up to 400 days.", code="invalid_range")
    return analytics.series(
        db, auth.org_id, _currency(db, auth, currency), metric, start, end, dimension, top=top
    )


@router.get("/monthly")
def monthly(
    auth: Reader,
    db: SessionDep,
    currency: CurrencyQ = None,
    metric: MetricQ = "effective",
    dimension: DimensionQ = "none",
    months: Annotated[int, Query(ge=1, le=36)] = 12,
    top: Annotated[int, Query(ge=1, le=20)] = 7,
) -> dict[str, Any]:
    assert auth.org_id is not None  # noqa: S101
    end = _today()
    start = end.replace(day=1)
    for _ in range(months - 1):
        start = (start - timedelta(days=1)).replace(day=1)
    return analytics.series(
        db,
        auth.org_id,
        _currency(db, auth, currency),
        metric,
        start,
        end,
        dimension,
        top=top,
        granularity="month",
    )


@router.get("/movers")
def movers(
    auth: Reader,
    db: SessionDep,
    currency: CurrencyQ = None,
    metric: MetricQ = "effective",
    dimension: DimensionQ = "service",
    days: Annotated[int, Query(ge=1, le=90)] = 7,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    include_purchases: bool = False,
) -> dict[str, Any]:
    assert auth.org_id is not None  # noqa: S101
    end = analytics.latest_date(db, auth.org_id) or _today()
    return analytics.movers(
        db,
        auth.org_id,
        _currency(db, auth, currency),
        metric,
        dimension,
        end,
        days,
        limit,
        include_purchases,
    )


@router.get("/early-warnings")
def early_warnings(auth: Reader, db: SessionDep) -> list[dict[str, Any]]:
    """Spend running hot: month-end forecast vs last month, and today vs the usual."""
    from costlens.costs.early_warnings import current

    assert auth.org_id is not None  # noqa: S101
    return current(db, auth.org_id)


@router.get("/network")
def network(
    auth: Reader,
    db: SessionDep,
    days: Annotated[int, Query(ge=7, le=90)] = 30,
    currency: Annotated[str | None, Query(pattern=r"^[A-Z]{3}$")] = None,
) -> dict[str, Any]:
    """Data-transfer costs by kind (between zones, regions, to the internet, NAT...)."""
    from costlens.costs.network import network_costs

    assert auth.org_id is not None  # noqa: S101
    return network_costs(db, auth.org_id, days=days, currency=currency)


@router.get("/providers")
def providers(
    auth: Reader, db: SessionDep, currency: CurrencyQ = None, metric: MetricQ = "effective"
) -> dict[str, Any]:
    """Every cloud and SaaS source side by side in one currency (2.16)."""
    assert auth.org_id is not None  # noqa: S101
    return analytics.by_provider(db, auth.org_id, _currency(db, auth, currency), metric, _today())
