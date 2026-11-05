"""Data quality report, required tags, tag normalisation rules."""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from costlens.accounts.roles import Permission
from costlens.api.deps import SessionDep, require
from costlens.auth.web import AuthContext
from costlens.core.errors import AppError
from costlens.costs.analytics import latest_date
from costlens.costs.explorer import TAG_KEY
from costlens.costs.quality import QualitySettings, quality_settings, report
from costlens.costs.tagrules import TagRule, org_rules
from costlens.db.base import utcnow
from costlens.models import Organization

router = APIRouter(tags=["data quality"])
Reader = Annotated[AuthContext, require(Permission.READ)]
Admin = Annotated[AuthContext, require(Permission.MANAGE_SETTINGS)]


def _org(db: SessionDep, auth: AuthContext) -> Organization:
    org = db.get(Organization, auth.org_id)
    assert org is not None  # noqa: S101
    return org


@router.get("/api/v1/costs/data-quality")
def data_quality(
    auth: Reader,
    db: SessionDep,
    days: Annotated[int, Query(ge=1, le=400)] = 30,
    currency: Annotated[str | None, Query(pattern=r"^[A-Z]{3}$")] = None,
) -> dict[str, Any]:
    assert auth.org_id is not None  # noqa: S101
    end = latest_date(db, auth.org_id) or utcnow().date()
    return report(db, auth.org_id, end - timedelta(days=days - 1), end, currency)


@router.get("/api/v1/org/data-quality-settings", response_model=QualitySettings)
def get_settings(auth: Reader, db: SessionDep) -> QualitySettings:
    return quality_settings(_org(db, auth))


@router.put("/api/v1/org/data-quality-settings", response_model=QualitySettings)
def put_settings(body: QualitySettings, auth: Admin, db: SessionDep) -> QualitySettings:
    bad = [k for k in body.required_tags if not TAG_KEY.match(k)]
    if bad:
        raise AppError("Not valid tag keys: " + ", ".join(bad), code="invalid_tag")
    org = _org(db, auth)
    org.settings = {**(org.settings or {}), "data_quality": body.model_dump()}
    db.flush()
    return body


class RulesIn(BaseModel):
    rules: list[TagRule] = Field(max_length=500)


@router.get("/api/v1/org/tag-rules")
def get_rules(auth: Reader, db: SessionDep) -> dict[str, Any]:
    return {"rules": [r.model_dump(by_alias=True) for r in org_rules(_org(db, auth))]}


@router.put("/api/v1/org/tag-rules")
def put_rules(body: RulesIn, auth: Admin, db: SessionDep) -> dict[str, Any]:
    """Replace the rules. New imports use them; call apply to fix existing data."""
    for rule in body.rules:
        if rule.kind == "value" and not rule.key:
            raise AppError("Value rules need a tag key.", code="invalid_rule")
    org = _org(db, auth)
    org.settings = {
        **(org.settings or {}),
        "tag_rules": [r.model_dump(by_alias=True) for r in body.rules],
    }
    db.flush()
    return {"rules": [r.model_dump(by_alias=True) for r in body.rules]}


@router.post("/api/v1/org/tag-rules/apply", status_code=202)
def apply(auth: Admin, db: SessionDep, request: Request) -> dict[str, str]:
    """Rewrite existing cost data with the current rules (background job)."""
    request.app.state.jobs.enqueue("costs.reapply_tag_rules", org_id=str(auth.org_id))
    return {"status": "queued"}
