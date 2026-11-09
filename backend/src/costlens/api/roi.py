"""ROI pack for finance (JSON, CSV and PDF) and the price paid for CostLens."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field

from costlens.accounts.roles import Permission
from costlens.api.deps import SessionDep, require
from costlens.auth.web import AuthContext
from costlens.db.base import utcnow
from costlens.models import Organization
from costlens.reports import roi

router = APIRouter(prefix="/api/v1/roi", tags=["roi"])
Reader = Annotated[AuthContext, require(Permission.READ)]
Settings = Annotated[AuthContext, require(Permission.MANAGE_SETTINGS)]


class RoiSettings(BaseModel):
    monthly_price: float | None = Field(default=None, ge=0, le=1e9)
    currency: str = Field(default="USD", pattern="^[A-Z]{3}$")
    since: date | None = None


def _period(
    db: SessionDep, auth: AuthContext, start: date | None, end: date | None
) -> tuple[date, date]:
    org = db.get(Organization, auth.org_id)
    since = roi.settings_of(org)["since"]
    end = end or utcnow().date()
    start = start or (
        date.fromisoformat(since) if since else (end - timedelta(days=365)).replace(day=1)
    )
    return start, end


@router.get("/executive")
def executive(auth: Reader, db: SessionDep) -> dict[str, Any]:
    """The executive summary page."""
    from costlens.reports.executive import summary

    assert auth.org_id is not None  # noqa: S101
    return summary(db, auth.org_id)


@router.get("/settings", response_model=RoiSettings)
def get_settings(auth: Reader, db: SessionDep) -> RoiSettings:
    return RoiSettings.model_validate(roi.settings_of(db.get(Organization, auth.org_id)))


@router.put("/settings", response_model=RoiSettings)
def put_settings(body: RoiSettings, auth: Settings, db: SessionDep) -> RoiSettings:
    org = db.get(Organization, auth.org_id)
    assert org is not None  # noqa: S101
    org.settings = {**(org.settings or {}), "roi": body.model_dump(mode="json")}
    db.flush()
    return body


@router.get("")
def get_roi(
    auth: Reader,
    db: SessionDep,
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
) -> dict[str, Any]:
    s, e = _period(db, auth, start, end)
    return roi.build(db, auth.org_id, s, e)


@router.get("/pack.csv")
def roi_csv(
    auth: Reader,
    db: SessionDep,
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
) -> Response:
    s, e = _period(db, auth, start, end)
    return Response(
        roi.to_csv(roi.build(db, auth.org_id, s, e)),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="costlens-roi-{s}-{e}.csv"'},
    )


@router.get("/pack.pdf")
def roi_pdf(
    auth: Reader,
    db: SessionDep,
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
) -> Response:
    s, e = _period(db, auth, start, end)
    return Response(
        roi.to_pdf(roi.build(db, auth.org_id, s, e)),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="costlens-roi-{s}-{e}.pdf"'},
    )
