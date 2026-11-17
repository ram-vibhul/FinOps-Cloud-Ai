"""What-if planner (11.39)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ccradar.accounts.roles import Permission
from ccradar.api.deps import SessionDep, require
from ccradar.auth.web import AuthContext
from ccradar.costs import whatif
from ccradar.db.base import utcnow
from ccradar.models import Organization

router = APIRouter(prefix="/api/v1/what-if", tags=["what-if"])
Reader = Annotated[AuthContext, require(Permission.READ)]
Lever = Literal["graviton", "schedule", "rightsizing", "waste", "other"]


class SavingsPlanIn(BaseModel):
    term: Literal["1yr", "3yr"] = "1yr"
    payment: Literal["no_upfront", "partial_upfront", "all_upfront"] = "no_upfront"
    percentile: float = Field(default=10, ge=0, le=50)


class WhatIfIn(BaseModel):
    levers: list[Lever] = Field(default_factory=list, max_length=5)
    savings_plan: SavingsPlanIn | None = None


@router.post("")
def what_if(body: WhatIfIn, auth: Reader, db: SessionDep) -> dict[str, Any]:
    """What the monthly bill would be after the chosen changes, on the bill's own rates."""
    assert auth.org_id is not None  # noqa: S101
    org = db.get(Organization, auth.org_id)
    currency = org.default_currency if org else "USD"
    sp = body.savings_plan.model_dump() if body.savings_plan else None
    return whatif.plan(db, auth.org_id, currency, list(body.levers), sp, utcnow().date())
