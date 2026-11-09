"""Cost explorer, filter values, tag keys and saved views."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import or_, select

from costlens.accounts.roles import Permission, Role
from costlens.api.deps import SessionDep, plan_feature, require
from costlens.auth.web import AuthContext
from costlens.core.errors import AppError, NotFoundError, PermissionDeniedError
from costlens.costs import ask as ask_ai
from costlens.costs import explorer
from costlens.costs.analytics import latest_date
from costlens.costs.anomalies import alert_settings
from costlens.db.base import utcnow
from costlens.models import Organization, SavedView

router = APIRouter(tags=["cost explorer"])
Reader = Annotated[AuthContext, require(Permission.READ)]
Writer = Annotated[AuthContext, require(Permission.WRITE)]
Admin = Annotated[AuthContext, require(Permission.MANAGE_SETTINGS)]


def _range(db: SessionDep, auth: AuthContext, days: int) -> tuple[date, date]:
    assert auth.org_id is not None  # noqa: S101
    end = latest_date(db, auth.org_id) or utcnow().date()
    return end - timedelta(days=days - 1), end


@router.post("/api/v1/costs/explore")
def explore(body: explorer.ExploreQuery, auth: Reader, db: SessionDep) -> dict[str, Any]:
    from costlens.accounts.custom_roles import explore_filters

    assert auth.org_id is not None  # noqa: S101
    if auth.scope:  # custom roles limited to accounts/teams (8.8)
        body = body.model_copy(update={"filters": explore_filters(auth.scope, body.filters)})
    return explorer.explore(db, auth.org_id, body)


class AskBody(BaseModel):
    question: str = Field(min_length=3, max_length=300)


@router.post("/api/v1/costs/ask", dependencies=[plan_feature("ai")])
def ask(body: AskBody, request: Request, auth: Reader, db: SessionDep) -> dict[str, Any]:
    """Turn a sentence into a report-builder query. Returns the query; does not run it."""
    assert auth.org_id is not None  # noqa: S101
    writer = request.app.state.ai
    org = db.get(Organization, auth.org_id)
    if writer is None:
        raise AppError("AI is not set up on this server.", status=409, code="ai_disabled")
    if org is None or not alert_settings(org).ai_explanations:
        raise AppError(
            "AI is turned off for this organisation. An admin can turn it on in Settings.",
            status=409,
            code="ai_off",
        )
    from costlens.billing.metering import use_ai

    use_ai(db, request.app.state.settings, strict=True)  # B.12: counted against the plan
    return ask_ai.ask(db, auth.org_id, body.question, writer)


@router.get("/api/v1/costs/dimensions/{dimension}/values")
def values(
    dimension: str,
    auth: Reader,
    db: SessionDep,
    q: Annotated[str | None, Query(max_length=100)] = None,
    days: Annotated[int, Query(ge=1, le=400)] = 90,
) -> list[dict[str, Any]]:
    try:
        explorer.check_dimension(dimension)
    except ValueError as exc:
        raise AppError(str(exc), code="unknown_dimension") from exc
    start, end = _range(db, auth, days)
    assert auth.org_id is not None  # noqa: S101
    out = explorer.dimension_values(db, auth.org_id, dimension, start=start, end=end, search=q)
    if auth.scope:  # only values inside a limited role's accounts/teams (8.8)
        allowed = {"account": auth.scope.get("accounts"), "team": auth.scope.get("teams")}.get(
            dimension
        )
        if allowed:
            out = [v for v in out if v["value"] in allowed]
        elif dimension not in ("service", "region", "charge_category", "pricing_category"):
            # Resource IDs and tag values could reveal other accounts: not offered.
            raise PermissionDeniedError("Your role is limited to some accounts or teams.")
    return out


@router.get("/api/v1/costs/tags")
def tags(
    auth: Reader, db: SessionDep, days: Annotated[int, Query(ge=1, le=400)] = 30
) -> list[dict[str, Any]]:
    start, end = _range(db, auth, days)
    assert auth.org_id is not None  # noqa: S101
    return explorer.tag_keys(db, auth.org_id, start=start, end=end)


class DimensionSettings(BaseModel):
    team_tag: str = Field(default="team", pattern=r"^[\w.:/@+=\- ]{1,128}$")
    project_tag: str = Field(default="project", pattern=r"^[\w.:/@+=\- ]{1,128}$")


@router.get("/api/v1/org/dimension-settings", response_model=DimensionSettings)
def get_dimension_settings(auth: Reader, db: SessionDep) -> DimensionSettings:
    org = db.get(Organization, auth.org_id)
    return DimensionSettings.model_validate(
        ((org.settings if org else None) or {}).get("dimensions", {})
    )


@router.put("/api/v1/org/dimension-settings", response_model=DimensionSettings)
def put_dimension_settings(
    body: DimensionSettings, auth: Admin, db: SessionDep
) -> DimensionSettings:
    """Which tag keys mean "team" and "project" in this organisation."""
    org = db.get(Organization, auth.org_id)
    assert org is not None  # noqa: S101
    org.settings = {**(org.settings or {}), "dimensions": body.model_dump()}
    db.flush()
    return body


# --- saved views ------------------------------------------------------------------


class ViewIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    shared: bool = False
    query: dict[str, Any]


class ViewOut(BaseModel):
    id: uuid.UUID
    name: str
    shared: bool
    query: dict[str, Any]
    owner_id: uuid.UUID | None
    mine: bool = False
    updated_at: datetime


def _view_out(view: SavedView, user_id: uuid.UUID) -> ViewOut:
    out = ViewOut.model_validate(view, from_attributes=True)
    out.mine = view.owner_id == user_id
    return out


def _validate_query(query: dict[str, Any]) -> dict[str, Any]:
    # Relative ranges are stored as "days"; absolute dates are allowed too.
    days = query.get("days")
    probe = {k: v for k, v in query.items() if k != "days"}
    probe.setdefault("start", "2026-01-01")
    probe.setdefault("end", "2026-01-31")
    try:
        explorer.ExploreQuery.model_validate(probe)
    except ValueError as exc:
        raise AppError(f"That view is not valid: {exc}", code="invalid_view") from exc
    if days is not None and not (isinstance(days, int) and 1 <= days <= 400):
        raise AppError("days must be between 1 and 400.", code="invalid_view")
    return query


def _editable(view: SavedView, auth: AuthContext) -> None:
    if view.owner_id != auth.user.id and not (auth.role and auth.role.at_least(Role.ADMIN)):
        raise PermissionDeniedError("Only the owner or an admin can change this view.")


@router.get("/api/v1/views", response_model=list[ViewOut])
def list_views(auth: Reader, db: SessionDep) -> list[ViewOut]:
    rows = db.scalars(
        select(SavedView)
        .where(or_(SavedView.shared.is_(True), SavedView.owner_id == auth.user.id))
        .order_by(SavedView.name)
    )
    return [_view_out(v, auth.user.id) for v in rows]


@router.post("/api/v1/views", response_model=ViewOut, status_code=201)
def create_view(body: ViewIn, auth: Writer, db: SessionDep) -> ViewOut:
    view = SavedView(
        org_id=auth.org_id,
        name=body.name.strip(),
        owner_id=auth.user.id,
        shared=body.shared,
        query=_validate_query(body.query),
    )
    db.add(view)
    db.flush()
    return _view_out(view, auth.user.id)


@router.put("/api/v1/views/{view_id}", response_model=ViewOut)
def update_view(view_id: uuid.UUID, body: ViewIn, auth: Writer, db: SessionDep) -> ViewOut:
    view = db.get(SavedView, view_id)
    if view is None or (not view.shared and view.owner_id != auth.user.id):
        raise NotFoundError("View not found.")
    _editable(view, auth)
    view.name, view.shared, view.query = body.name.strip(), body.shared, _validate_query(body.query)
    db.flush()
    return _view_out(view, auth.user.id)


@router.delete("/api/v1/views/{view_id}", status_code=204)
def delete_view(view_id: uuid.UUID, auth: Writer, db: SessionDep) -> Response:
    view = db.get(SavedView, view_id)
    if view is None or (not view.shared and view.owner_id != auth.user.id):
        raise NotFoundError("View not found.")
    _editable(view, auth)
    db.delete(view)
    db.flush()
    return Response(status_code=204)
