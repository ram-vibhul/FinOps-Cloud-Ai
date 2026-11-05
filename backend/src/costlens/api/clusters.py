"""Kubernetes clusters: connect (token + install command), list, rotate, remove."""

from __future__ import annotations

import re
import shlex
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from costlens.accounts.roles import Permission
from costlens.api.deps import SessionDep, SettingsDep, require
from costlens.auth.web import AuthContext
from costlens.core.errors import AppError, NotFoundError
from costlens.db.base import utcnow
from costlens.k8s.service import LATEST_AGENT, issue_token
from costlens.models import K8sCluster, K8sObject

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
    health: str = "waiting"  # waiting | ok | silent | outdated
    objects: dict[str, int] = {}


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
    if c.last_seen_at is None:
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
        f"helm upgrade --install costlens-agent {settings.agent_chart} "
        f"--namespace costlens --create-namespace "
        f"--set costlens.url={shlex.quote(settings.base_url)} "
        f"--set costlens.clusterName={shlex.quote(name)} "
        f"--set costlens.token={shlex.quote(token)}"
    )


def _get(db: SessionDep, cluster_id: uuid.UUID) -> K8sCluster:
    c = db.get(K8sCluster, cluster_id)
    if c is None:
        raise NotFoundError("Cluster not found.")
    return c


@router.get("", response_model=list[ClusterOut])
def list_clusters(auth: Reader, db: SessionDep) -> list[ClusterOut]:
    return [_out(db, c) for c in db.scalars(select(K8sCluster).order_by(K8sCluster.name))]


@router.post("", response_model=ClusterWithToken, status_code=201)
def create_cluster(
    body: ClusterIn, auth: Admin, db: SessionDep, settings: SettingsDep
) -> dict[str, Any]:
    if db.scalar(select(K8sCluster.id).where(K8sCluster.name == body.name)):
        raise AppError("A cluster with this name already exists.", status=409, code="duplicate")
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
