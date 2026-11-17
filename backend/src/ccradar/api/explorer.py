"""Cost explorer, filter values, tag keys and saved views."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import or_, select

from ccradar.accounts.roles import Permission, Role
from ccradar.api.deps import SessionDep, plan_feature, require
from ccradar.auth.web import AuthContext
from ccradar.core.errors import AppError, NotFoundError, PermissionDeniedError
from ccradar.costs import ask as ask_ai
from ccradar.costs import explorer
from ccradar.costs.analytics import latest_date
from ccradar.costs.anomalies import alert_settings
from ccradar.db.base import utcnow
from ccradar.models import Organization, SavedView

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
    from ccradar.accounts.custom_roles import explore_filters

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
    from ccradar.billing.metering import use_ai

    use_ai(db, request.app.state.settings, strict=True)  # B.12: counted against the plan
    return ask_ai.ask(db, auth.org_id, body.question, writer)


class InvestigateBody(BaseModel):
    days: int = Field(default=7, ge=1, le=90)


@router.post("/api/v1/costs/investigate")
def investigate_change(
    body: InvestigateBody, request: Request, auth: Reader, db: SessionDep
) -> dict[str, Any]:
    """Why did cost change over the last ``days`` billed days (11.32)? The evidence is
    gathered without AI; with AI turned on, the summary is reworded in plain words."""
    from ccradar.costs.analytics import latest_date
    from ccradar.costs.investigate import investigate

    assert auth.org_id is not None  # noqa: S101
    end = latest_date(db, auth.org_id)
    if end is None:
        raise AppError("No billing data yet.", status=409, code="no_data")
    org = db.get(Organization, auth.org_id)
    writer = request.app.state.ai
    if writer is not None and org is not None and alert_settings(org).ai_explanations:
        from ccradar.billing.metering import use_ai

        try:
            use_ai(db, request.app.state.settings, strict=True)
        except AppError:
            writer = None  # over the plan's AI allowance: facts only
    else:
        writer = None
    start = end - timedelta(days=body.days - 1)
    return investigate(db, auth.org_id, start, end, writer)


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
        .where(
            SavedView.kind == "cost_explorer",
            or_(SavedView.shared.is_(True), SavedView.owner_id == auth.user.id),
        )
        .order_by(SavedView.name)
    )
    return [_view_out(v, auth.user.id) for v in rows]


# --- custom dashboards (11.24): a named page of saved views -----------------------------


class WidgetIn(BaseModel):
    view_id: uuid.UUID
    title: str | None = Field(default=None, max_length=120)
    chart: Literal["bar", "line", "table"] = "bar"


class DashboardIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    shared: bool = True
    widgets: list[WidgetIn] = Field(default_factory=list, max_length=12)


def _visible(db: SessionDep, auth: AuthContext, kind: str) -> Any:
    return select(SavedView).where(
        SavedView.kind == kind,
        or_(SavedView.shared.is_(True), SavedView.owner_id == auth.user.id),
    )


def _dashboard_out(db: SessionDep, d: SavedView, auth: AuthContext) -> dict[str, Any]:
    views = {v.id: v for v in db.scalars(_visible(db, auth, "cost_explorer"))}
    widgets = []
    for w in (d.query or {}).get("widgets") or []:
        v = views.get(uuid.UUID(w["view_id"]))
        if v is not None:  # a view deleted or made private since: left out
            widgets.append({**w, "title": w.get("title") or v.name, "query": v.query})
    return {
        "id": str(d.id),
        "name": d.name,
        "shared": d.shared,
        "mine": d.owner_id == auth.user.id,
        "widgets": widgets,
    }


def _dashboard_query(db: SessionDep, auth: AuthContext, body: DashboardIn) -> dict[str, Any]:
    visible = {v.id for v in db.scalars(_visible(db, auth, "cost_explorer"))}
    missing = [str(w.view_id) for w in body.widgets if w.view_id not in visible]
    if missing:
        raise AppError("A widget uses a view you cannot see.", code="invalid_view")
    return {"widgets": [{**w.model_dump(), "view_id": str(w.view_id)} for w in body.widgets]}


@router.get("/api/v1/dashboards")
def list_dashboards(auth: Reader, db: SessionDep) -> list[dict[str, Any]]:
    rows = db.scalars(_visible(db, auth, "dashboard").order_by(SavedView.name))
    return [_dashboard_out(db, d, auth) for d in rows]


@router.post("/api/v1/dashboards", status_code=201)
def create_dashboard(body: DashboardIn, auth: Writer, db: SessionDep) -> dict[str, Any]:
    d = SavedView(
        org_id=auth.org_id,
        name=body.name.strip(),
        kind="dashboard",
        owner_id=auth.user.id,
        shared=body.shared,
        query=_dashboard_query(db, auth, body),
    )
    db.add(d)
    db.flush()
    return _dashboard_out(db, d, auth)


def _dashboard(db: SessionDep, auth: AuthContext, dashboard_id: uuid.UUID) -> SavedView:
    d = db.get(SavedView, dashboard_id)
    if d is None or d.kind != "dashboard" or (not d.shared and d.owner_id != auth.user.id):
        raise NotFoundError("Dashboard not found.")
    _editable(d, auth)
    return d


@router.put("/api/v1/dashboards/{dashboard_id}")
def update_dashboard(
    dashboard_id: uuid.UUID, body: DashboardIn, auth: Writer, db: SessionDep
) -> dict[str, Any]:
    d = _dashboard(db, auth, dashboard_id)
    d.name, d.shared, d.query = body.name.strip(), body.shared, _dashboard_query(db, auth, body)
    db.flush()
    return _dashboard_out(db, d, auth)


@router.delete("/api/v1/dashboards/{dashboard_id}", status_code=204)
def delete_dashboard(dashboard_id: uuid.UUID, auth: Writer, db: SessionDep) -> Response:
    db.delete(_dashboard(db, auth, dashboard_id))
    db.flush()
    return Response(status_code=204)


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
