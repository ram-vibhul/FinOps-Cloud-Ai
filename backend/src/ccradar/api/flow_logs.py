"""VPC Flow Logs: find them, turn attribution on, and see who causes network cost."""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from ccradar.accounts.roles import Permission
from ccradar.api.deps import SessionDep, require
from ccradar.auth.web import AuthContext
from ccradar.clouds.aws.client import AwsAccessError
from ccradar.core.errors import AppError, NotFoundError
from ccradar.costs import flow_logs
from ccradar.costs.network import CATEGORIES
from ccradar.db.tenant import set_tenant
from ccradar.models import CloudConnection, NetworkAttribution

router = APIRouter(tags=["network"])
Reader = Annotated[AuthContext, require(Permission.READ)]
Admin = Annotated[AuthContext, require(Permission.MANAGE_CONNECTIONS)]


class Source(BaseModel):
    type: Literal["s3", "cloudwatch"]
    bucket: str | None = Field(default=None, max_length=255)
    prefix: str = Field(default="", max_length=500)
    log_group: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def _complete(self) -> Source:
        if self.type == "s3" and not self.bucket:
            raise ValueError("bucket is required for S3")
        if self.type == "cloudwatch" and not self.log_group:
            raise ValueError("log_group is required for CloudWatch")
        return self


class FlowSettings(BaseModel):
    enabled: bool = False
    sources: list[Source] = Field(default_factory=list, max_length=20)


def _connection(db: SessionDep, connection_id: uuid.UUID) -> CloudConnection:
    conn = db.get(CloudConnection, connection_id)
    if conn is None or conn.provider != "aws":
        raise NotFoundError("Connection not found.")
    return conn


def _view(conn: CloudConnection) -> dict[str, Any]:
    s = (conn.settings or {}).get("flow_logs") or {}
    sources = s.get("sources") or []
    return {
        "enabled": bool(s.get("enabled")),
        "sources": sources,
        "last_run": s.get("last_run"),
        "lines": s.get("lines"),
        "errors": s.get("errors") or {},
        "policy_statements": flow_logs.policy_statements(sources),
    }


@router.get("/api/v1/connections/{connection_id}/flow-logs")
def get_flow_logs(connection_id: uuid.UUID, auth: Reader, db: SessionDep) -> dict[str, Any]:
    return _view(_connection(db, connection_id))


@router.post("/api/v1/connections/{connection_id}/flow-logs/discover")
def discover_flow_logs(
    connection_id: uuid.UUID, auth: Admin, db: SessionDep, request: Request
) -> dict[str, Any]:
    """List the flow logs set up in the account (``ec2:DescribeFlowLogs``, read-only)."""
    from ccradar.inventory.service import billed_regions

    conn = _connection(db, connection_id)
    if not conn.role_arn:
        raise AppError("Finish connecting the account first.", code="not_connected")
    gateway = request.app.state.aws
    try:
        session = gateway.assume(conn.role_arn, conn.external_id, f"ccradar-{conn.org_id}")
    except AwsAccessError as error:
        raise AppError("AWS refused the role.", code="access_denied") from error
    found: list[dict[str, Any]] = []
    for region in billed_regions(db, conn.org_id, conn.account_id):
        try:
            found += flow_logs.discover(gateway.client(session, "ec2", region=region))
        except Exception:  # noqa: S112 - a region without access: skip it
            continue
    unique = {tuple(sorted((k, str(v)) for k, v in f.items() if k != "resource")): f for f in found}
    sources = [{k: v for k, v in f.items() if k != "resource"} for f in unique.values()]
    return {"sources": sources, "policy_statements": flow_logs.policy_statements(sources)}


@router.put("/api/v1/connections/{connection_id}/flow-logs")
def put_flow_logs(
    connection_id: uuid.UUID, body: FlowSettings, auth: Admin, db: SessionDep
) -> dict[str, Any]:
    conn = _connection(db, connection_id)
    current = (conn.settings or {}).get("flow_logs") or {}
    conn.settings = {
        **(conn.settings or {}),
        "flow_logs": {
            **current,
            "enabled": body.enabled,
            "sources": [s.model_dump() for s in body.sources],
        },
    }
    db.flush()
    return _view(conn)


@router.post("/api/v1/connections/{connection_id}/flow-logs/run")
def run_flow_logs(
    connection_id: uuid.UUID, auth: Admin, db: SessionDep, request: Request
) -> dict[str, Any]:
    """Read yesterday's flow logs now (it also runs daily)."""
    conn = _connection(db, connection_id)
    result = flow_logs.collect(db, request.app.state.aws, conn.id)
    assert auth.org_id is not None  # noqa: S101
    set_tenant(db, org_id=auth.org_id)
    if not result:
        raise AppError("Turn flow-log attribution on and add a source first.", code="not_enabled")
    return {k: v for k, v in result.items() if k != "labels"}


@router.get("/api/v1/costs/network/attribution")
def attribution(auth: Reader, db: SessionDep) -> dict[str, Any]:
    """Who causes NAT, cross-zone, inter-region and internet transfer cost."""
    rows = list(
        db.scalars(select(NetworkAttribution).order_by(NetworkAttribution.cost_monthly.desc()))
    )
    teams: dict[str, float] = {}
    for r in rows:
        teams[r.team or "Unassigned"] = teams.get(r.team or "Unassigned", 0.0) + r.cost_monthly
    return {
        "computed_at": rows[0].computed_at.isoformat() if rows else None,
        "currency": rows[0].currency if rows else None,
        "categories": {k: CATEGORIES[k] for k in flow_logs.ATTRIBUTED},
        "items": [
            {
                "category": r.category,
                "owner": r.owner_label,
                "owner_kind": r.owner_kind,
                "team": r.team,
                "gb": round(r.bytes / 1e9, 3),
                "share_pct": r.share_pct,
                "cost_monthly": r.cost_monthly,
                "az": (r.details or {}).get("az"),
            }
            for r in rows
        ],
        "teams": [
            {"team": k, "cost_monthly": round(v, 2)}
            for k, v in sorted(teams.items(), key=lambda kv: -kv[1])
        ],
    }
