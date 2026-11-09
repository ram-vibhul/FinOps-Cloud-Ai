"""Cost anomalies and alert settings."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from costlens.accounts.roles import Permission
from costlens.api.deps import SessionDep, require
from costlens.auth.web import AuthContext
from costlens.core.errors import NotFoundError
from costlens.costs.anomalies import AlertSettings, alert_settings
from costlens.models import CloudAccount, CostAnomaly, Organization

router = APIRouter(tags=["anomalies"])
Reader = Annotated[AuthContext, require(Permission.READ)]
Writer = Annotated[AuthContext, require(Permission.WRITE)]
Admin = Annotated[AuthContext, require(Permission.MANAGE_SETTINGS)]


class AnomalyOut(BaseModel):
    id: uuid.UUID
    day: date
    last_day: date
    days: int
    peak_impact: float
    label: str = ""
    dimension: str
    key: str
    currency: str
    actual: float
    expected: float
    upper: float
    impact: float
    score: float
    status: str
    note: str | None
    created_at: datetime


class AnomalyPatch(BaseModel):
    status: Literal["open", "acknowledged", "expected", "resolved"]
    note: str | None = Field(default=None, max_length=1000)


@router.get("/api/v1/costs/anomalies", response_model=list[AnomalyOut])
def list_anomalies(
    auth: Reader,
    db: SessionDep,
    status: Annotated[
        Literal["open", "acknowledged", "expected", "resolved", "all"], Query()
    ] = "open",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[AnomalyOut]:
    stmt = select(CostAnomaly).order_by(CostAnomaly.last_day.desc(), CostAnomaly.impact.desc())
    if status != "all":
        stmt = stmt.where(CostAnomaly.status == status)
    rows = list(db.scalars(stmt.limit(limit)))
    names = dict(db.execute(select(CloudAccount.external_id, CloudAccount.name)).all())
    out = []
    for a in rows:
        item = AnomalyOut.model_validate(a, from_attributes=True)
        name = names.get(a.key) if a.dimension == "account" else None
        if a.dimension == "total":
            item.label = "All spend"
        else:
            item.label = f"{name} ({a.key})" if name else a.key
        out.append(item)
    return out


@router.patch("/api/v1/costs/anomalies/{anomaly_id}", response_model=AnomalyOut)
def update_anomaly(
    anomaly_id: uuid.UUID, body: AnomalyPatch, auth: Writer, db: SessionDep
) -> AnomalyOut:
    """Mark an anomaly acknowledged, expected (not a problem) or resolved."""
    anomaly = db.get(CostAnomaly, anomaly_id)
    if anomaly is None:
        raise NotFoundError("Anomaly not found.")
    anomaly.status, anomaly.note, anomaly.status_by_id = body.status, body.note, auth.user.id
    db.flush()
    return AnomalyOut.model_validate(anomaly, from_attributes=True)


@router.get("/api/v1/costs/anomalies/{anomaly_id}/explanation")
def explain_anomaly(
    anomaly_id: uuid.UUID, auth: Reader, db: SessionDep, request: Request
) -> dict[str, Any]:
    """What moved, by how much, the likely driver and related changes, in plain words."""
    from costlens.costs.explain import explain

    anomaly = db.get(CostAnomaly, anomaly_id)
    if anomaly is None:
        raise NotFoundError("Anomaly not found.")
    result = explain(db, anomaly)
    writer = request.app.state.ai
    org = db.get(Organization, auth.org_id)
    if writer is not None and org is not None and alert_settings(org).ai_explanations:
        facts = (
            result["text"]
            + "\nDrivers: "
            + "; ".join(
                f"{d['by']}={d['value']} +{d['increase_per_day']} {anomaly.currency}/day"
                for d in result["drivers"]
            )
        )
        from costlens.billing.metering import use_ai

        worded = writer.rewrite(facts) if use_ai(db, request.app.state.settings) else None
        if worded:
            result.update(text=worded, template_text=result["text"], worded_by="ai")
    return result


@router.get("/api/v1/org/alert-settings", response_model=AlertSettings)
def get_alert_settings(auth: Reader, db: SessionDep) -> AlertSettings:
    org = db.get(Organization, auth.org_id)
    assert org is not None  # noqa: S101
    return alert_settings(org)


@router.put("/api/v1/org/alert-settings", response_model=AlertSettings)
def put_alert_settings(body: AlertSettings, auth: Admin, db: SessionDep) -> AlertSettings:
    org = db.get(Organization, auth.org_id)
    assert org is not None  # noqa: S101
    org.settings = {**(org.settings or {}), "alerts": body.model_dump()}
    db.flush()
    return body
