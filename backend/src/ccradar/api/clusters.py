"""Kubernetes clusters: connect (token + install command), list, rotate, remove."""

from __future__ import annotations

import hashlib
import re
import shlex
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ccradar.accounts.roles import Permission
from ccradar.api.deps import SessionDep, SettingsDep, plan_feature, require
from ccradar.auth.web import AuthContext
from ccradar.core.errors import AppError, NotFoundError
from ccradar.db.base import utcnow
from ccradar.k8s.service import LATEST_AGENT, agent_config, issue_token
from ccradar.models import K8sCluster, K8sObject

router = APIRouter(prefix="/api/v1/clusters", tags=["kubernetes"])
Reader = Annotated[AuthContext, require(Permission.READ)]
Admin = Annotated[AuthContext, require(Permission.MANAGE_CONNECTIONS)]
NAME = r"^[a-z0-9]([a-z0-9-]{0,98}[a-z0-9])?$"
SILENT_AFTER_MINUTES = 15


class ClusterIn(BaseModel):
    name: str = Field(pattern=NAME, description="Lower-case letters, digits and dashes.")


class ClusterOut(BaseModel):
    id: uuid.UUID
    name: str
    status: str
    agent_version: str | None
    kubernetes_version: str | None
    provider: str | None
    node_count: int | None
    last_seen_at: datetime | None
    created_at: datetime
    token_hint: str
    health: str = "waiting"  # waiting | ok | silent | outdated | revoked
    objects: dict[str, int] = {}
    first_scan: dict[str, Any] | None = None
    namespaces: list[str] = []
    include_namespaces: list[str] = []
    exclude_namespaces: list[str] = []
    agent: dict[str, Any] = {}  # usage source and Prometheus address the agent reports
    prices: dict[str, Any] | None = None  # custom prices (None = list prices / the bill)


class ClusterWithToken(ClusterOut):
    token: str
    install_command: str


def _out(db: SessionDep, c: K8sCluster) -> ClusterOut:
    out = ClusterOut.model_validate(c, from_attributes=True)
    counts = db.execute(
        select(K8sObject.kind, func.count())
        .where(K8sObject.cluster_id == c.id, K8sObject.deleted_at.is_(None))
        .group_by(K8sObject.kind)
    ).all()
    out.objects = {k: int(n) for k, n in counts}
    out.first_scan = (c.settings or {}).get("first_scan")
    out.agent = (c.settings or {}).get("agent") or {}
    out.prices = (c.settings or {}).get("prices")
    cfg = agent_config(c)
    out.include_namespaces, out.exclude_namespaces = (
        cfg["include_namespaces"],
        cfg["exclude_namespaces"],
    )
    out.namespaces = sorted(
        db.scalars(
            select(K8sObject.name)
            .where(
                K8sObject.cluster_id == c.id,
                K8sObject.kind == "Namespace",
                K8sObject.deleted_at.is_(None),
            )
            .limit(500)
        )
    )
    if c.status == "revoked":
        out.health = "revoked"
    elif c.last_seen_at is None:
        out.health = "waiting"
    else:
        seen = c.last_seen_at if c.last_seen_at.tzinfo else c.last_seen_at.replace(tzinfo=UTC)
        if (utcnow() - seen).total_seconds() > SILENT_AFTER_MINUTES * 60:
            out.health = "silent"
        elif c.agent_version and _version(c.agent_version) < _version(LATEST_AGENT):
            out.health = "outdated"
        else:
            out.health = "ok"
    return out


def _version(value: str) -> tuple[int, ...]:
    core = re.split(r"[-+]", value, maxsplit=1)[0]
    try:
        return tuple(int(x) for x in core.split("."))
    except ValueError:
        return (0,)


def _install(settings: SettingsDep, token: str, name: str) -> str:
    return (
        f"helm upgrade --install cloudcostradar-agent {settings.agent_chart} "
        f"--namespace ccradar --create-namespace "
        f"--set ccradar.url={shlex.quote(settings.base_url)} "
        f"--set ccradar.clusterName={shlex.quote(name)} "
        f"--set ccradar.token={shlex.quote(token)}"
    )


def _get(db: SessionDep, cluster_id: uuid.UUID) -> K8sCluster:
    c = db.get(K8sCluster, cluster_id)
    if c is None:
        raise NotFoundError("Cluster not found.")
    return c


@router.get("", response_model=list[ClusterOut])
def list_clusters(auth: Reader, db: SessionDep) -> list[ClusterOut]:
    return [_out(db, c) for c in db.scalars(select(K8sCluster).order_by(K8sCluster.name))]


@router.post(
    "", response_model=ClusterWithToken, status_code=201, dependencies=[plan_feature("kubernetes")]
)
def create_cluster(
    body: ClusterIn, auth: Admin, db: SessionDep, settings: SettingsDep
) -> dict[str, Any]:
    if db.scalar(select(K8sCluster.id).where(K8sCluster.name == body.name)):
        raise AppError("A cluster with this name already exists.", status=409, code="duplicate")
    from ccradar.billing.usage import enforce
    from ccradar.models import Organization

    org = db.get(Organization, auth.org_id)
    if org is not None:
        enforce(db, org, settings, "clusters")
    c = K8sCluster(id=uuid.uuid4(), org_id=auth.org_id, name=body.name, status="waiting")
    token = issue_token(c)
    db.add(c)
    db.flush()
    return {
        **_out(db, c).model_dump(),
        "token": token,
        "install_command": _install(settings, token, c.name),
    }


@router.post("/{cluster_id}/rotate-token", response_model=ClusterWithToken)
def rotate_token(
    cluster_id: uuid.UUID, auth: Admin, db: SessionDep, settings: SettingsDep
) -> dict[str, Any]:
    """New token; the old one stops working immediately."""
    c = _get(db, cluster_id)
    token = issue_token(c)
    if c.status == "revoked":
        c.status = "waiting"
    db.flush()
    return {
        **_out(db, c).model_dump(),
        "token": token,
        "install_command": _install(settings, token, c.name),
    }


@router.delete("/{cluster_id}", status_code=204)
def delete_cluster(cluster_id: uuid.UUID, auth: Admin, db: SessionDep) -> Response:
    """Disconnect: the token stops working and the cluster's data is removed."""
    db.delete(_get(db, cluster_id))
    db.flush()
    return Response(status_code=204)


NS = r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$"


class ClusterPatch(BaseModel):
    name: str | None = Field(default=None, pattern=NAME)
    include_namespaces: list[Annotated[str, Field(pattern=NS)]] | None = Field(
        default=None, max_length=200
    )
    exclude_namespaces: list[Annotated[str, Field(pattern=NS)]] | None = Field(
        default=None, max_length=200
    )


@router.patch("/{cluster_id}", response_model=ClusterOut)
def update_cluster(
    cluster_id: uuid.UUID, body: ClusterPatch, auth: Admin, db: SessionDep
) -> ClusterOut:
    """Rename, or choose which namespaces the agent watches (it picks this up within
    its next check-in). Include wins: when set, only those namespaces are watched."""
    c = _get(db, cluster_id)
    if body.name and body.name != c.name:
        if db.scalar(select(K8sCluster.id).where(K8sCluster.name == body.name)):
            raise AppError("A cluster with this name already exists.", status=409, code="duplicate")
        c.name = body.name
    settings = dict(c.settings or {})
    if body.include_namespaces is not None:
        settings["include_namespaces"] = sorted(set(body.include_namespaces))
    if body.exclude_namespaces is not None:
        settings["exclude_namespaces"] = sorted(set(body.exclude_namespaces))
    c.settings = settings
    db.flush()
    return _out(db, c)


@router.post("/{cluster_id}/revoke", response_model=ClusterOut)
def revoke_token(cluster_id: uuid.UUID, auth: Admin, db: SessionDep) -> ClusterOut:
    """Stop the agent now: its token stops working and no new token is issued."""
    import secrets as _secrets

    c = _get(db, cluster_id)
    c.token_hash = hashlib.sha256(_secrets.token_bytes(32)).hexdigest()  # nobody holds this
    c.token_hint, c.status = "revoked", "revoked"
    db.flush()
    return _out(db, c)


class PricesIn(BaseModel):
    """Your own prices: used where the bill has no line for a node (clusters outside AWS,
    on-premises), or always when ``always`` is set."""

    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    cpu_hour: float = Field(ge=0, le=100, description="per vCPU per hour")
    gib_hour: float = Field(ge=0, le=10, description="per GiB of memory per hour")
    node_hour: float = Field(default=0, ge=0, le=1000, description="flat fee per node per hour")
    spot_discount_pct: float = Field(default=0, ge=0, le=95)
    always: bool = False


@router.put("/{cluster_id}/prices", response_model=ClusterOut)
def set_prices(cluster_id: uuid.UUID, body: PricesIn, auth: Admin, db: SessionDep) -> ClusterOut:
    """Applies from the next hourly allocation; past hours keep the prices they had."""
    c = _get(db, cluster_id)
    c.settings = {**(c.settings or {}), "prices": body.model_dump()}
    db.flush()
    return _out(db, c)


@router.delete("/{cluster_id}/prices", response_model=ClusterOut)
def clear_prices(cluster_id: uuid.UUID, auth: Admin, db: SessionDep) -> ClusterOut:
    c = _get(db, cluster_id)
    c.settings = {k: v for k, v in (c.settings or {}).items() if k != "prices"}
    db.flush()
    return _out(db, c)
