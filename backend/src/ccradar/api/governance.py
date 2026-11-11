"""Governance policies and their violations."""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from ccradar.accounts.roles import Permission
from ccradar.api.deps import SessionDep, require
from ccradar.auth.web import AuthContext
from ccradar.core.errors import NotFoundError
from ccradar.costs import governance
from ccradar.db.base import utcnow
from ccradar.models import Organization, Policy, PolicyViolation

router = APIRouter(prefix="/api/v1/governance", tags=["governance"])
Reader = Annotated[AuthContext, require(Permission.READ)]
Writer = Annotated[AuthContext, require(Permission.WRITE)]
Admin = Annotated[AuthContext, require(Permission.MANAGE_SETTINGS)]


class PolicyIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["required_tags", "allowed_regions", "denied_instance_types", "k8s_requests"]
    params: dict[str, Any] = Field(default_factory=dict)
    resource_types: list[str] = Field(default_factory=list, max_length=30)
    severity: Literal["info", "warning", "critical"] = "warning"
    enabled: bool = True

    @model_validator(mode="after")
    def _params(self) -> PolicyIn:
        need = {
            "required_tags": "tags",
            "allowed_regions": "regions",
            "denied_instance_types": "patterns",
        }.get(self.kind)
        if need and not (isinstance(self.params.get(need), list) and self.params[need]):
            raise ValueError(f"params.{need} must list at least one value")
        return self


class ExemptIn(BaseModel):
    note: str = Field(min_length=1, max_length=1000)


def _org(auth: AuthContext) -> uuid.UUID:
    assert auth.org_id is not None  # noqa: S101
    return auth.org_id


def _run(db: SessionDep, auth: AuthContext) -> dict[str, int]:
    org = db.get(Organization, _org(auth))
    return governance.evaluate(db, _org(auth), org.default_currency if org else "USD")


@router.get("/policies")
def list_policies(auth: Reader, db: SessionDep) -> list[dict[str, Any]]:
    return governance.summary(db)


@router.post("/policies", status_code=201)
def create_policy(body: PolicyIn, auth: Admin, db: SessionDep) -> dict[str, Any]:
    p = Policy(org_id=auth.org_id, **body.model_dump())
    db.add(p)
    db.flush()
    _run(db, auth)
    return next(x for x in governance.summary(db) if x["id"] == str(p.id))


@router.put("/policies/{policy_id}")
def update_policy(
    policy_id: uuid.UUID, body: PolicyIn, auth: Admin, db: SessionDep
) -> dict[str, Any]:
    p = db.get(Policy, policy_id)
    if p is None:
        raise NotFoundError("Policy not found.")
    for field, value in body.model_dump().items():
        setattr(p, field, value)
    db.flush()
    _run(db, auth)
    return next(x for x in governance.summary(db) if x["id"] == str(p.id))


@router.delete("/policies/{policy_id}", status_code=204)
def delete_policy(policy_id: uuid.UUID, auth: Admin, db: SessionDep) -> Response:
    p = db.get(Policy, policy_id)
    if p is None:
        raise NotFoundError("Policy not found.")
    db.delete(p)
    db.flush()
    return Response(status_code=204)


@router.post("/run")
def run_now(auth: Admin, db: SessionDep) -> dict[str, int]:
    return _run(db, auth)


@router.get("/violations")
def violations(
    auth: Reader,
    db: SessionDep,
    policy_id: uuid.UUID | None = None,
    status: Annotated[Literal["open", "resolved", "exempt", "all"], Query()] = "open",
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> list[dict[str, Any]]:
    q = select(PolicyViolation, Policy.name, Policy.severity).join(
        Policy, Policy.id == PolicyViolation.policy_id
    )
    if policy_id:
        q = q.where(PolicyViolation.policy_id == policy_id)
    if status != "all":
        q = q.where(PolicyViolation.status == status)
    rows = db.execute(
        q.order_by(
            PolicyViolation.monthly_cost.desc().nulls_last(), PolicyViolation.first_seen_at
        ).limit(limit)
    ).all()
    return [
        {
            "id": str(v.id),
            "policy_id": str(v.policy_id),
            "policy": name,
            "severity": severity,
            "resource": v.resource_key,
            "resource_type": v.resource_type,
            "name": v.name,
            "account_id": v.account_id,
            "region": v.region,
            "detail": v.detail,
            "monthly_cost": v.monthly_cost,
            "status": v.status,
            "note": v.note,
            "first_seen_at": v.first_seen_at.isoformat(),
        }
        for v, name, severity in rows
    ]


@router.post("/violations/{violation_id}/exempt")
def exempt(violation_id: uuid.UUID, body: ExemptIn, auth: Writer, db: SessionDep) -> dict[str, str]:
    v = db.get(PolicyViolation, violation_id)
    if v is None:
        raise NotFoundError("Violation not found.")
    v.status, v.note, v.resolved_at = "exempt", body.note.strip(), utcnow()
    db.flush()
    return {"status": v.status}


@router.delete("/violations/{violation_id}/exempt")
def unexempt(violation_id: uuid.UUID, auth: Writer, db: SessionDep) -> dict[str, str]:
    v = db.get(PolicyViolation, violation_id)
    if v is None:
        raise NotFoundError("Violation not found.")
    v.status, v.note, v.resolved_at = "open", None, None
    db.flush()
    return {"status": v.status}
