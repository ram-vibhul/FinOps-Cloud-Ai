"""Peer benchmarks (11.40): opt in, then compare four ratios with other organisations."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter
from pydantic import BaseModel

from ccradar.accounts.roles import Permission
from ccradar.api.deps import SessionDep, require
from ccradar.auth.web import AuthContext
from ccradar.core.errors import AppError
from ccradar.costs import benchmarks
from ccradar.models import Organization

router = APIRouter(prefix="/api/v1/benchmarks", tags=["benchmarks"])
Reader = Annotated[AuthContext, require(Permission.READ)]
Admin = Annotated[AuthContext, require(Permission.MANAGE_SETTINGS)]


class OptIn(BaseModel):
    opted_in: bool


@router.get("")
def get_benchmarks(auth: Reader, db: SessionDep) -> dict[str, Any]:
    """Your ratios for the last full month and, once you share them, your peers'."""
    assert auth.org_id is not None  # noqa: S101
    return benchmarks.view(db, auth.org_id)


@router.put("/opt-in")
def set_opt_in(body: OptIn, auth: Admin, db: SessionDep) -> dict[str, Any]:
    """Share (or stop sharing) the four ratios; stopping deletes what was shared."""
    assert auth.org_id is not None  # noqa: S101
    org = db.get(Organization, auth.org_id)
    if org is None:
        raise AppError("Organisation not found.", code="not_found", status=404)
    if org.is_demo:
        raise AppError("The demo organisation cannot share benchmarks.", code="demo")
    org.settings = {**(org.settings or {}), "benchmarks": body.opted_in}
    db.flush()
    benchmarks.refresh(db, auth.org_id)
    return benchmarks.view(db, auth.org_id)
