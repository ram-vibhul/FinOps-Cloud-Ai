"""Carbon reporting (11.26)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query

from ccradar.accounts.roles import Permission
from ccradar.api.deps import SessionDep, require
from ccradar.auth.web import AuthContext
from ccradar.costs import carbon
from ccradar.db.base import utcnow

router = APIRouter(prefix="/api/v1/carbon", tags=["carbon"])
Reader = Annotated[AuthContext, require(Permission.READ)]


@router.get("")
def carbon_report(
    auth: Reader, db: SessionDep, months: Annotated[int, Query(ge=1, le=24)] = 6
) -> dict[str, Any]:
    """Estimated emissions per month, service and region (kg CO2e)."""
    assert auth.org_id is not None  # noqa: S101
    return carbon.report(db, auth.org_id, months, utcnow().date())
