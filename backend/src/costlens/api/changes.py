"""Change events, the cost timeline, and GitHub webhooks."""

from __future__ import annotations

import json
import secrets
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from costlens.accounts.roles import Permission
from costlens.api.deps import SessionDep, SettingsDep, require
from costlens.auth.web import AuthContext
from costlens.changes import github
from costlens.changes.service import ChangeIn, cost_effect, record, related_events
from costlens.core.errors import AppError, NotFoundError
from costlens.costs.analytics import latest_date
from costlens.db.base import utcnow
from costlens.db.tenant import set_tenant
from costlens.models import ChangeEvent, CostAnomaly, CostDaily, InboundWebhook

router = APIRouter(tags=["changes"])
Reader = Annotated[AuthContext, require(Permission.READ)]
Writer = Annotated[AuthContext, require(Permission.WRITE)]
Admin = Annotated[AuthContext, require(Permission.MANAGE_CONNECTIONS)]


class ChangeOut(BaseModel):
    id: uuid.UUID
    occurred_at: datetime
    source: str
    kind: str
    title: str
    description: str | None
    service: str | None
    account: str | None
    region: str | None
    environment: str | None
    url: str | None
    actor: str | None
    effect: dict[str, Any] | None = None


def _out(db: SessionDep, org_id: uuid.UUID, e: ChangeEvent, with_effect: bool) -> ChangeOut:
    out = ChangeOut.model_validate(e, from_attributes=True)
    if with_effect:
        out.effect = cost_effect(db, org_id, e)
    return out


@router.post("/api/v1/changes", response_model=ChangeOut, status_code=201)
def create_change(body: ChangeIn, auth: Writer, db: SessionDep) -> ChangeOut:
    """Record a deployment or change (from a person, a script or CI)."""
    assert auth.org_id is not None  # noqa: S101
    event, _ = record(db, auth.org_id, body, source="api", created_by=auth.user.id)
    return _out(db, auth.org_id, event, with_effect=False)


@router.get("/api/v1/changes", response_model=list[ChangeOut])
def list_changes(
    auth: Reader,
    db: SessionDep,
    days: Annotated[int, Query(ge=1, le=400)] = 30,
    service: str | None = None,
) -> list[ChangeOut]:
    assert auth.org_id is not None  # noqa: S101
    since = utcnow() - timedelta(days=days)
    stmt = select(ChangeEvent).where(ChangeEvent.occurred_at >= since)
    if service:
        stmt = stmt.where(ChangeEvent.service == service)
    rows = db.scalars(stmt.order_by(ChangeEvent.occurred_at.desc()).limit(200)).all()
    return [_out(db, auth.org_id, e, with_effect=True) for e in rows]


@router.delete("/api/v1/changes/{change_id}", status_code=204)
def delete_change(change_id: uuid.UUID, auth: Writer, db: SessionDep) -> Response:
    event = db.get(ChangeEvent, change_id)
    if event is None:
        raise NotFoundError("Change not found.")
    db.delete(event)
    db.flush()
    return Response(status_code=204)


@router.get("/api/v1/costs/timeline")
def timeline(
    auth: Reader, db: SessionDep, days: Annotated[int, Query(ge=7, le=400)] = 90
) -> dict[str, Any]:
    """Daily usage cost with the changes that happened, for one chart."""
    assert auth.org_id is not None  # noqa: S101
    end = latest_date(db, auth.org_id) or utcnow().date()
    start = end - timedelta(days=days - 1)
    rows = db.execute(
        select(CostDaily.charge_date, func.sum(CostDaily.effective_cost))
        .where(
            CostDaily.org_id == auth.org_id,
            CostDaily.charge_date >= start,
            CostDaily.charge_date <= end,
            CostDaily.charge_category.in_(("Usage", "Adjustment")),
        )
        .group_by(CostDaily.charge_date)
    ).all()
    daily = {d: float(v or 0) for d, v in rows}
    since = datetime.combine(start, datetime.min.time(), UTC)
    events = db.scalars(
        select(ChangeEvent)
        .where(ChangeEvent.occurred_at >= since)
        .order_by(ChangeEvent.occurred_at)
    ).all()
    periods = [(start + timedelta(days=i)) for i in range(days)]
    return {
        "periods": [p.isoformat() for p in periods],
        "values": [round(daily.get(p, 0.0), 2) for p in periods],
        "changes": [
            _out(db, auth.org_id, e, with_effect=True).model_dump(mode="json") for e in events
        ],
    }


@router.get("/api/v1/costs/anomalies/{anomaly_id}/changes", response_model=list[ChangeOut])
def anomaly_changes(anomaly_id: uuid.UUID, auth: Reader, db: SessionDep) -> list[ChangeOut]:
    """Changes that may explain this anomaly."""
    anomaly = db.get(CostAnomaly, anomaly_id)
    if anomaly is None:
        raise NotFoundError("Anomaly not found.")
    assert auth.org_id is not None  # noqa: S101
    return [_out(db, auth.org_id, e, with_effect=False) for e in related_events(db, anomaly)]


# --- GitHub webhooks ------------------------------------------------------------


class WebhookIn(BaseModel):
    name: str = Field(default="GitHub", min_length=1, max_length=120)


class WebhookOut(BaseModel):
    id: uuid.UUID
    kind: str
    name: str
    url: str
    last_received_at: datetime | None
    secret: str | None = None  # only in the creation response


@router.post("/api/v1/org/webhooks/github", response_model=WebhookOut, status_code=201)
def create_github_webhook(
    body: WebhookIn, auth: Admin, db: SessionDep, settings: SettingsDep
) -> WebhookOut:
    secret = secrets.token_urlsafe(32)
    hook = InboundWebhook(org_id=auth.org_id, kind="github", name=body.name, secret=secret)
    db.add(hook)
    db.flush()
    url = f"{settings.base_url.rstrip('/')}/api/v1/hooks/github/{hook.id}"
    return WebhookOut(
        id=hook.id, kind=hook.kind, name=hook.name, url=url, last_received_at=None, secret=secret
    )


@router.get("/api/v1/org/webhooks", response_model=list[WebhookOut])
def list_webhooks(auth: Admin, db: SessionDep, settings: SettingsDep) -> list[WebhookOut]:
    base = settings.base_url.rstrip("/")
    return [
        WebhookOut(
            id=h.id,
            kind=h.kind,
            name=h.name,
            last_received_at=h.last_received_at,
            url=f"{base}/api/v1/hooks/{h.kind}/{h.id}",
        )
        for h in db.scalars(select(InboundWebhook).order_by(InboundWebhook.created_at))
    ]


@router.delete("/api/v1/org/webhooks/{hook_id}", status_code=204)
def delete_webhook(hook_id: uuid.UUID, auth: Admin, db: SessionDep) -> Response:
    hook = db.get(InboundWebhook, hook_id)
    if hook is None:
        raise NotFoundError("Webhook not found.")
    db.delete(hook)
    db.flush()
    return Response(status_code=204)


@router.post("/api/v1/hooks/github/{hook_id}", status_code=202, include_in_schema=False)
async def github_webhook(
    hook_id: uuid.UUID,
    request: Request,
    db: SessionDep,
    x_github_event: Annotated[str | None, Header()] = None,
    x_hub_signature_256: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    body = await request.body()
    if len(body) > 5 * 1024 * 1024:
        raise AppError("Payload too large.", status=413, code="too_large")
    set_tenant(db, bypass=True)
    hook = db.get(InboundWebhook, hook_id)
    if (
        hook is None
        or hook.kind != "github"
        or not github.valid_signature(hook.secret, body, x_hub_signature_256)
    ):
        raise AppError("Unknown webhook or bad signature.", status=401, code="bad_signature")
    org_id = hook.org_id
    hook.last_received_at = utcnow()
    set_tenant(db, org_id=org_id)
    try:
        payload = json.loads(body or b"{}")
    except json.JSONDecodeError as exc:
        raise AppError("Body is not JSON.", code="bad_payload") from exc
    change = github.to_change(x_github_event or "", payload)
    if change is None:
        return {"status": "ignored"}
    _, created = record(db, org_id, change, source="github")
    return {"status": "recorded" if created else "duplicate"}


__all__ = ["date", "router"]
