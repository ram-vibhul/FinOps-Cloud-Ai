"""Budgets: nested to any depth, tracked with a forecast, alerting by email, Slack and Teams."""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from costlens.accounts.roles import Permission
from costlens.api.deps import SessionDep, require
from costlens.auth.web import AuthContext
from costlens.core.errors import AppError, NotFoundError
from costlens.costs import budgets
from costlens.costs.explorer import check_dimension
from costlens.models import Budget

router = APIRouter(prefix="/api/v1/budgets", tags=["budgets"])
Reader = Annotated[AuthContext, require(Permission.READ)]
Writer = Annotated[AuthContext, require(Permission.WRITE)]
Admin = Annotated[AuthContext, require(Permission.MANAGE_SETTINGS)]


class BudgetIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    parent_id: uuid.UUID | None = None
    amount: float = Field(gt=0, le=1e12)
    currency: str = Field(default="USD", pattern="^[A-Z]{3}$")
    period: Literal["monthly", "quarterly", "annual"] = "monthly"
    metric: Literal["effective", "billed"] = "effective"
    filters: dict[str, list[str]] = Field(default_factory=dict)
    thresholds: list[int] = Field(default_factory=lambda: [80, 100], max_length=6)
    alert_on_forecast: bool = True
    recipients: list[str] = Field(default_factory=list, max_length=20)
    owner_id: uuid.UUID | None = None

    @field_validator("filters")
    @classmethod
    def _filters(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        for dim, values in value.items():
            check_dimension(dim)
            if not values or len(values) > 100:
                raise ValueError(f"{dim}: give 1 to 100 values")
        return value

    @field_validator("recipients")
    @classmethod
    def _emails(cls, value: list[str]) -> list[str]:
        import re

        for e in value:
            if not re.fullmatch(r"[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,63}", e):
                raise ValueError(f"not an email address: {e}")
        return [e.lower() for e in value]

    @field_validator("thresholds")
    @classmethod
    def _thresholds(cls, value: list[int]) -> list[int]:
        if any(t < 1 or t > 200 for t in value):
            raise ValueError("thresholds are percentages from 1 to 200")
        return sorted(set(value))


def _org(auth: AuthContext) -> uuid.UUID:
    assert auth.org_id is not None  # noqa: S101 - require() guarantees an organisation
    return auth.org_id


def _check_parent(db: SessionDep, body: BudgetIn, self_id: uuid.UUID | None = None) -> None:
    pid = body.parent_id
    seen = {self_id} if self_id else set()
    while pid is not None:
        if pid in seen:
            raise AppError("A budget cannot sit under itself.", code="budget_cycle")
        seen.add(pid)
        parent = db.get(Budget, pid)
        if parent is None:
            raise NotFoundError("Parent budget not found.")
        pid = parent.parent_id


def _one(db: SessionDep, auth: AuthContext, b: Budget) -> dict[str, Any]:
    by_id = {x.id: x for x in db.scalars(select(Budget))}
    from costlens.db.base import utcnow

    return budgets.status(db, _org(auth), b, by_id, utcnow().date())


@router.get("")
def list_budgets(auth: Reader, db: SessionDep) -> list[dict[str, Any]]:
    """Every budget with spend, forecast and children (build the tree from parent_id)."""
    return sorted(
        budgets.all_status(db, _org(auth)), key=lambda s: (s["parent_id"] or "", s["name"])
    )


@router.post("", status_code=201)
def create_budget(body: BudgetIn, auth: Writer, db: SessionDep) -> dict[str, Any]:
    _check_parent(db, body)
    b = Budget(
        org_id=auth.org_id,
        alert_state={},
        **{**body.model_dump(), "recipients": [str(r) for r in body.recipients]},
    )
    db.add(b)
    db.flush()
    return _one(db, auth, b)


@router.put("/{budget_id}")
def update_budget(
    budget_id: uuid.UUID, body: BudgetIn, auth: Writer, db: SessionDep
) -> dict[str, Any]:
    b = db.get(Budget, budget_id)
    if b is None:
        raise NotFoundError("Budget not found.")
    _check_parent(db, body, b.id)
    for field, value in body.model_dump().items():
        setattr(b, field, value)
    b.alert_state = {}  # a changed budget starts its alerts afresh
    db.flush()
    return _one(db, auth, b)


@router.delete("/{budget_id}", status_code=204)
def delete_budget(budget_id: uuid.UUID, auth: Writer, db: SessionDep) -> Response:
    b = db.get(Budget, budget_id)
    if b is None:
        raise NotFoundError("Budget not found.")
    db.delete(b)  # children go with it
    db.flush()
    return Response(status_code=204)


@router.post("/check")
def check_now(auth: Admin, db: SessionDep, request: Request) -> dict[str, int]:
    """Check thresholds now (also runs every morning)."""
    return {"alerts": budgets.check_alerts(db, _org(auth), enqueue=request.app.state.jobs.enqueue)}
