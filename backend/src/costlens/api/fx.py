"""Exchange-rate overrides: an organisation's own rates (e.g. finance's fixed monthly
rate) that take precedence over the daily reference rates."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import or_, select

from costlens.accounts.roles import Permission
from costlens.api.deps import SessionDep, require
from costlens.auth.web import AuthContext
from costlens.core.errors import AppError, NotFoundError
from costlens.costs import fx
from costlens.models import FxOverride

router = APIRouter(prefix="/api/v1/fx", tags=["fx"])
Reader = Annotated[AuthContext, require(Permission.READ)]
Admin = Annotated[AuthContext, require(Permission.MANAGE_SETTINGS)]
CURRENCY = r"^[A-Z]{3}$"


class OverrideIn(BaseModel):
    from_currency: str = Field(pattern=CURRENCY)
    to_currency: str = Field(pattern=CURRENCY)
    start_day: date
    end_day: date
    rate: Decimal = Field(gt=0, le=Decimal("1000000000"), decimal_places=10)
    note: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def _check(self) -> OverrideIn:
        if self.from_currency == self.to_currency:
            raise ValueError("Choose two different currencies.")
        if self.end_day < self.start_day:
            raise ValueError("The end date must not be before the start date.")
        if (self.end_day - self.start_day).days > 400:
            raise ValueError("An override can cover at most 400 days.")
        return self


class OverrideOut(OverrideIn):
    id: uuid.UUID


@router.get("/overrides", response_model=list[OverrideOut])
def list_overrides(auth: Reader, db: SessionDep) -> list[OverrideOut]:
    rows = db.scalars(
        select(FxOverride).order_by(FxOverride.start_day.desc(), FxOverride.from_currency)
    )
    return [OverrideOut.model_validate(r, from_attributes=True) for r in rows]


@router.post("/overrides", response_model=OverrideOut, status_code=201)
def create_override(body: OverrideIn, auth: Admin, db: SessionDep) -> OverrideOut:
    pair = (body.from_currency, body.to_currency)
    clash = db.scalar(
        select(FxOverride.id).where(
            or_(
                (FxOverride.from_currency == pair[0]) & (FxOverride.to_currency == pair[1]),
                (FxOverride.from_currency == pair[1]) & (FxOverride.to_currency == pair[0]),
            ),
            FxOverride.start_day <= body.end_day,
            FxOverride.end_day >= body.start_day,
        )
    )
    if clash is not None:
        raise AppError(
            "Another override for these currencies covers some of these days. "
            "Delete it first or choose other dates.",
            status=409,
            code="overlapping_override",
        )
    row = FxOverride(org_id=auth.org_id, **body.model_dump())
    db.add(row)
    db.flush()
    return OverrideOut.model_validate(row, from_attributes=True)


@router.delete("/overrides/{override_id}", status_code=204)
def delete_override(override_id: uuid.UUID, auth: Admin, db: SessionDep) -> Response:
    row = db.get(FxOverride, override_id)
    if row is None:
        raise NotFoundError("Override not found.")
    db.delete(row)
    db.flush()
    return Response(status_code=204)


@router.get("/rate")
def get_rate(
    auth: Reader,
    db: SessionDep,
    day: Annotated[date, Query()],
    source: Annotated[str, Query(pattern=CURRENCY)],
    target: Annotated[str, Query(pattern=CURRENCY)],
) -> dict[str, Any]:
    """The rate CostLens uses for a day, and whether it is the organisation's override."""
    used = fx.rate(db, day, source, target, org_id=auth.org_id)
    reference = fx.rate(db, day, source, target)
    return {
        "day": day.isoformat(),
        "source": source,
        "target": target,
        "rate": float(used) if used is not None else None,
        "reference_rate": float(reference) if reference is not None else None,
        "override": used is not None and used != reference,
    }
