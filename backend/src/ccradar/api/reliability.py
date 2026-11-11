"""Reliability targets (SLOs) that savings recommendations must respect."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from ccradar.accounts.roles import Permission
from ccradar.api.deps import SessionDep, require
from ccradar.auth.web import AuthContext
from ccradar.core.errors import NotFoundError
from ccradar.models import ReliabilityTarget

router = APIRouter(prefix="/api/v1/reliability-targets", tags=["reliability"])
Reader = Annotated[AuthContext, require(Permission.READ)]
Admin = Annotated[AuthContext, require(Permission.MANAGE_SETTINGS)]


class Match(BaseModel):
    kind: Literal["k8s_workload", "k8s_namespace", "aws_resource", "aws_tag"]
    cluster_id: str | None = Field(default=None, max_length=64)
    namespace: str | None = Field(default=None, max_length=253)
    workload: str | None = Field(default=None, max_length=253)
    resource_id: str | None = Field(default=None, max_length=1000)
    key: str | None = Field(default=None, max_length=128)
    value: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def _complete(self) -> Match:
        need = {
            "k8s_workload": ("cluster_id", "namespace", "workload"),
            "k8s_namespace": ("namespace",),
            "aws_resource": ("resource_id",),
            "aws_tag": ("key", "value"),
        }[self.kind]
        missing = [f for f in need if not getattr(self, f)]
        if missing:
            raise ValueError(f"missing {', '.join(missing)}")
        return self


class TargetIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    match: Match
    tier: Literal["critical", "standard", "best_effort"] = "standard"
    availability_pct: float = Field(default=99.9, ge=90, le=99.999)
    headroom_pct: float = Field(default=20, ge=0, le=300)
    max_utilization_pct: float = Field(default=80, ge=20, le=95)
    min_replicas: int = Field(default=1, ge=1, le=1000)
    notes: str = Field(default="", max_length=1000)


class TargetOut(TargetIn):
    id: uuid.UUID


def _out(t: ReliabilityTarget) -> TargetOut:
    return TargetOut(
        id=t.id,
        name=t.name,
        match=Match.model_validate(t.match),
        tier=t.tier,  # type: ignore[arg-type]
        availability_pct=t.availability_pct,
        headroom_pct=t.headroom_pct,
        max_utilization_pct=t.max_utilization_pct,
        min_replicas=t.min_replicas,
        notes=t.notes,
    )


@router.get("", response_model=list[TargetOut])
def list_targets(auth: Reader, db: SessionDep) -> list[TargetOut]:
    return [_out(t) for t in db.scalars(select(ReliabilityTarget).order_by(ReliabilityTarget.name))]


@router.post("", response_model=TargetOut, status_code=201)
def create_target(body: TargetIn, auth: Admin, db: SessionDep) -> TargetOut:
    t = ReliabilityTarget(
        org_id=auth.org_id,
        **{**body.model_dump(), "match": body.match.model_dump(exclude_none=True)},
    )
    db.add(t)
    db.flush()
    return _out(t)


@router.put("/{target_id}", response_model=TargetOut)
def update_target(target_id: uuid.UUID, body: TargetIn, auth: Admin, db: SessionDep) -> TargetOut:
    t = db.get(ReliabilityTarget, target_id)
    if t is None:
        raise NotFoundError("Target not found.")
    for field, value in {
        **body.model_dump(),
        "match": body.match.model_dump(exclude_none=True),
    }.items():
        setattr(t, field, value)
    db.flush()
    return _out(t)


@router.delete("/{target_id}", status_code=204)
def delete_target(target_id: uuid.UUID, auth: Admin, db: SessionDep) -> Response:
    t = db.get(ReliabilityTarget, target_id)
    if t is None:
        raise NotFoundError("Target not found.")
    db.delete(t)
    db.flush()
    return Response(status_code=204)
