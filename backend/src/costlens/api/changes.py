"""Change events, the cost timeline, and GitHub webhooks."""

from __future__ import annotations

import json
import secrets
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from costlens.accounts.roles import Permission
from costlens.api.deps import SessionDep, SettingsDep, require
from costlens.auth.web import AuthContext
from costlens.changes import github
from costlens.changes.service import ChangeIn, cost_effect, record, related_events
from costlens.config import Settings
from costlens.core.errors import AppError, NotFoundError
from costlens.costs.analytics import latest_date
from costlens.db.base import utcnow
from costlens.db.tenant import set_tenant
from costlens.models import ChangeEvent, CostAnomaly, CostDaily, InboundWebhook, K8sCluster

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


class AlertReceiverIn(BaseModel):
    kind: Literal["alertmanager", "pagerduty", "opsgenie"]
    name: str = Field(min_length=1, max_length=120)
    cluster_id: uuid.UUID


def _setup(kind: str, url: str, secret: str) -> str:
    if kind == "alertmanager":
        return (
            "receivers:\n  - name: costlens\n    webhook_configs:\n"
            f"      - url: {url}\n        send_resolved: true\n        http_config:\n"
            f"          authorization:\n            credentials: {secret}\n"
        )
    if kind == "pagerduty":
        return (
            f"PagerDuty -> Integrations -> Generic Webhooks (v3) -> New webhook: URL {url}, "
            "events incident.triggered and incident.resolved. PagerDuty then shows a signing "
            "secret: paste it into CostLens (Settings -> Webhooks -> Set signing secret)."
        )
    return (
        f"Opsgenie -> Settings -> Integrations -> Webhook: URL {url}, turn on 'Add Alert "
        f"Description/Details', custom header Authorization: Bearer {secret}. Put namespace and "
        "deployment (or pod) in the alert details or tags (key:value)."
    )


@router.post("/api/v1/org/webhooks/alerts", status_code=201)
def create_alert_receiver(
    body: AlertReceiverIn, auth: Admin, db: SessionDep, settings: SettingsDep
) -> dict[str, Any]:
    if db.get(K8sCluster, body.cluster_id) is None:
        raise NotFoundError("Cluster not found.")
    secret = secrets.token_urlsafe(32)
    hook = InboundWebhook(
        org_id=auth.org_id,
        kind=body.kind,
        name=body.name,
        secret=secret,
        cluster_id=body.cluster_id,
    )
    db.add(hook)
    db.flush()
    url = f"{settings.base_url.rstrip('/')}/api/v1/hooks/{hook.kind}/{hook.id}"
    out = WebhookOut(
        id=hook.id, kind=hook.kind, name=hook.name, url=url, last_received_at=None, secret=secret
    )
    return {**out.model_dump(mode="json"), "setup": _setup(body.kind, url, secret)}


async def _alert_delivery(
    kind: str, hook_id: uuid.UUID, request: Request, db: Session, settings: Settings
) -> dict[str, Any]:
    from costlens.k8s import alerts

    body = await request.body()
    if len(body) > 5 * 1024 * 1024:
        raise AppError("Payload too large.", status=413, code="too_large")
    set_tenant(db, bypass=True)
    hook = db.get(InboundWebhook, hook_id)
    headers = {k.lower(): v for k, v in request.headers.items()}
    if hook is None or hook.kind != kind or not alerts.verify(kind, hook.secret, body, headers):
        raise AppError("Unknown receiver or bad credentials.", status=401, code="bad_signature")
    set_tenant(db, org_id=hook.org_id)
    cluster = db.get(K8sCluster, hook.cluster_id) if hook.cluster_id else None
    if cluster is None:
        raise AppError("This receiver's cluster no longer exists.", status=410, code="no_cluster")
    try:
        payload = json.loads(body or b"{}")
    except json.JSONDecodeError as exc:
        raise AppError("Body is not JSON.", code="bad_payload") from exc
    found = alerts.parse(kind, payload if isinstance(payload, dict) else {})
    return {"status": "accepted", **alerts.handle(db, hook, cluster, found, settings.base_url)}


def _register_alert_route(kind: str) -> None:
    async def receive(
        hook_id: uuid.UUID, request: Request, db: SessionDep, settings: SettingsDep
    ) -> dict[str, Any]:
        return await _alert_delivery(kind, hook_id, request, db, settings)

    router.add_api_route(
        f"/api/v1/hooks/{kind}/{{hook_id}}",
        receive,
        methods=["POST"],
        status_code=202,
        include_in_schema=False,
        name=f"{kind}_webhook",
    )


for _kind in ("alertmanager", "pagerduty", "opsgenie"):
    _register_alert_route(_kind)


class SecretIn(BaseModel):
    secret: str = Field(min_length=8, max_length=500)


@router.put("/api/v1/org/webhooks/{hook_id}/secret", status_code=204)
def set_webhook_secret(hook_id: uuid.UUID, body: SecretIn, auth: Admin, db: SessionDep) -> Response:
    """Use a signing secret the other service generated (PagerDuty v3 creates its own)."""
    hook = db.get(InboundWebhook, hook_id)
    if hook is None:
        raise NotFoundError("Webhook not found.")
    hook.secret = body.secret
    db.flush()
    return Response(status_code=204)


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
