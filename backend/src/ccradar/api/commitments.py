"""Savings Plans / Reserved Instances: coverage, utilisation and what AWS recommends buying."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from ccradar.accounts.roles import Permission
from ccradar.api.deps import SessionDep, require
from ccradar.auth.web import AuthContext
from ccradar.core.errors import AppError, NotFoundError
from ccradar.db.base import utcnow
from ccradar.findings.commitments import latest
from ccradar.models import CloudConnection, Commitment, Contract

router = APIRouter(prefix="/api/v1/commitments", tags=["commitments"])
Reader = Annotated[AuthContext, require(Permission.READ)]
Admin = Annotated[AuthContext, require(Permission.MANAGE_CONNECTIONS)]
Settings = Annotated[AuthContext, require(Permission.MANAGE_SETTINGS)]


@router.get("")
def get_commitments(auth: Reader, db: SessionDep) -> dict[str, Any]:
    return {
        "accounts": [
            {
                "connection_id": str(snap.connection_id) if snap.connection_id else None,
                "name": name,
                "taken_at": snap.taken_at.isoformat(),
                **snap.data,
            }
            for snap, name in latest(db)
        ]
    }


@router.post("/refresh")
def refresh(auth: Admin, db: SessionDep, request: Request) -> dict[str, Any]:
    """Take a new snapshot now for every AWS connection (Cost Explorer: ~8 calls each)."""
    from ccradar.clouds.aws.commitment_job import snapshot

    ids = [
        c.id
        for c in db.scalars(
            select(CloudConnection).where(
                CloudConnection.provider == "aws", CloudConnection.status == "active"
            )
        )
    ]
    from ccradar.clouds.aws.commitment_inventory import collect

    taken = sum(snapshot(db, request.app.state.aws, cid) is not None for cid in ids)
    listed = sum(collect(db, request.app.state.aws, cid).get("count", 0) for cid in ids)
    return {"snapshots": taken, "commitments": listed}


class CommitmentOut(BaseModel):
    id: uuid.UUID
    kind: str
    commitment_id: str
    account_id: str | None
    region: str | None
    description: str
    count: int
    hourly_commitment: float | None
    upfront: float | None
    recurring_hourly: float | None
    currency: str
    term_years: float | None
    payment_option: str | None
    offering_class: str | None
    starts_at: datetime | None
    ends_at: datetime | None
    state: str
    utilization_pct: float | None
    days_left: int | None = None


class ContractIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    provider: Literal["aws", "azure", "gcp", "other"] = "aws"
    kind: Literal["edp", "private_pricing", "marketplace", "other"] = "edp"
    commitment_amount: float = Field(gt=0, le=1e12)
    currency: str = Field(default="USD", pattern="^[A-Z]{3}$")
    discount_pct: float | None = Field(default=None, ge=0, le=100)
    starts_on: date
    ends_on: date
    notes: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def _order(self) -> ContractIn:
        if self.ends_on <= self.starts_on:
            raise ValueError("ends_on must be after starts_on")
        return self


class ContractOut(ContractIn):
    id: uuid.UUID
    progress: dict[str, Any]


@router.get("/inventory")
def inventory(auth: Reader, db: SessionDep) -> dict[str, Any]:
    """Every Savings Plan, reservation and negotiated contract, soonest to expire first."""
    from ccradar.clouds.aws.commitment_inventory import KIND_LABELS, contract_progress

    today = utcnow().date()
    rows = db.scalars(
        select(Commitment).where(Commitment.state != "retired").order_by(Commitment.ends_at)
    ).all()
    items = []
    for c in rows:
        out = CommitmentOut.model_validate(c, from_attributes=True)
        out.days_left = (c.ends_at.date() - today).days if c.ends_at else None
        items.append(out)
    contracts = [
        ContractOut(
            **ContractIn.model_validate(k, from_attributes=True).model_dump(),
            id=k.id,
            progress=contract_progress(db, k, today),
        )
        for k in db.scalars(select(Contract).order_by(Contract.ends_on))
    ]
    return {"kinds": KIND_LABELS, "items": items, "contracts": contracts}


@router.post("/contracts", response_model=ContractOut, status_code=201)
def create_contract(body: ContractIn, auth: Settings, db: SessionDep) -> ContractOut:
    from ccradar.clouds.aws.commitment_inventory import contract_progress

    k = Contract(org_id=auth.org_id, alerts_sent=[], **body.model_dump())
    db.add(k)
    db.flush()
    return ContractOut(**body.model_dump(), id=k.id, progress=contract_progress(db, k))


@router.put("/contracts/{contract_id}", response_model=ContractOut)
def update_contract(
    contract_id: uuid.UUID, body: ContractIn, auth: Settings, db: SessionDep
) -> ContractOut:
    from ccradar.clouds.aws.commitment_inventory import contract_progress

    k = db.get(Contract, contract_id)
    if k is None:
        raise NotFoundError("Contract not found.")
    if k.ends_on != body.ends_on:
        k.alerts_sent = []  # a new end date gets its own reminders
    for field, value in body.model_dump().items():
        setattr(k, field, value)
    db.flush()
    return ContractOut(**body.model_dump(), id=k.id, progress=contract_progress(db, k))


@router.delete("/contracts/{contract_id}", status_code=204)
def delete_contract(contract_id: uuid.UUID, auth: Settings, db: SessionDep) -> Response:
    k = db.get(Contract, contract_id)
    if k is None:
        raise NotFoundError("Contract not found.")
    db.delete(k)
    db.flush()
    return Response(status_code=204)


@router.get("/plan")
def purchase_plan(
    auth: Reader,
    db: SessionDep,
    percentile: Annotated[float, Query(ge=0, le=50)] = 10,
    term: Literal["1yr", "3yr"] = "1yr",
    payment: Literal["no_upfront", "partial_upfront", "all_upfront"] = "no_upfront",
    rungs: Annotated[int, Query(ge=1, le=12)] = 4,
    interval_months: Annotated[int, Query(ge=1, le=12)] = 3,
    days: Annotated[int, Query(ge=14, le=90)] = 60,
) -> dict[str, Any]:
    """A conservative, laddered purchase plan (nothing is bought: CloudCostRadar is read-only)."""
    from ccradar.costs.commitment_plan import reservation_plan, savings_plan_plan
    from ccradar.models import Organization

    assert auth.org_id is not None  # noqa: S101
    org = db.get(Organization, auth.org_id)
    currency = org.default_currency if org else "USD"
    today = utcnow().date()
    return {
        "currency": currency,
        "settings": {
            "percentile": percentile,
            "term": term,
            "payment": payment,
            "rungs": rungs,
            "interval_months": interval_months,
            "days": days,
        },
        "savings_plan": savings_plan_plan(
            db,
            auth.org_id,
            currency,
            pct=percentile,
            term=term,
            payment=payment,
            rungs=rungs,
            interval_months=interval_months,
            days=days,
            today=today,
        ),
        "reservations": reservation_plan(
            db, auth.org_id, currency, pct=percentile, term=term, days=days
        ),
    }


Writer = Annotated[AuthContext, require(Permission.WRITE)]


class PolicyIO(BaseModel):
    max_monthly_commitment: float | None = Field(default=None, ge=0, le=1e10)
    currency: str = Field(default="USD", pattern="^[A-Z]{3}$")
    max_term: Literal["1yr", "3yr"] = "1yr"
    allowed_payments: list[Literal["no_upfront", "partial_upfront", "all_upfront"]] = Field(
        default_factory=lambda: ["no_upfront", "partial_upfront"], min_length=1
    )
    max_coverage_pct: float = Field(default=80, ge=10, le=100)
    preview_days: int = Field(default=7, ge=7, le=60)


@router.get("/policy", response_model=PolicyIO)
def get_policy(auth: Reader, db: SessionDep) -> PolicyIO:
    from ccradar.costs.purchases import policy_for

    assert auth.org_id is not None  # noqa: S101
    return PolicyIO.model_validate(policy_for(db, auth.org_id), from_attributes=True)


@router.put("/policy", response_model=PolicyIO)
def put_policy(body: PolicyIO, auth: Settings, db: SessionDep) -> PolicyIO:
    from ccradar.costs.purchases import policy_for

    assert auth.org_id is not None  # noqa: S101
    p = policy_for(db, auth.org_id)
    for field, value in body.model_dump().items():
        setattr(p, field, value)
    db.flush()
    return body


class PurchaseIn(BaseModel):
    kind: Literal["savings_plan", "reservation"]
    description: str = Field(min_length=1, max_length=300)
    hourly_commitment: float | None = Field(default=None, gt=0, le=1e6)
    instance_type: str | None = Field(default=None, max_length=100)
    count: int | None = Field(default=None, ge=1, le=10000)
    service: str | None = Field(default=None, max_length=100)
    region: str | None = Field(default=None, max_length=40)
    term: Literal["1yr", "3yr"] = "1yr"
    payment: Literal["no_upfront", "partial_upfront", "all_upfront"] = "no_upfront"
    monthly_cost: float = Field(ge=0, le=1e10)
    estimated_monthly_saving: float = Field(default=0, ge=0, le=1e10)
    planned_for: date
    notes: str = Field(default="", max_length=2000)


class PurchaseOut(PurchaseIn):
    id: uuid.UUID
    status: str
    currency: str
    commitment_id: str | None
    purchased_on: date | None
    verification: list[dict[str, Any]]
    preview_ends: date | None = None


class Bought(BaseModel):
    commitment_id: str = Field(min_length=3, max_length=300)
    purchased_on: date


def _purchase_out(x: Any) -> PurchaseOut:
    return PurchaseOut.model_validate(x, from_attributes=True)


def _steady_commit_hourly(db: SessionDep, org_id: uuid.UUID, currency: str) -> float | None:
    from ccradar.costs.commitment_plan import savings_plan_plan

    plan = savings_plan_plan(
        db,
        org_id,
        currency,
        pct=10,
        term="1yr",
        payment="no_upfront",
        rungs=1,
        interval_months=3,
        days=60,
        today=utcnow().date(),
    )
    if plan.get("status") != "ok":
        return None
    return plan["after_changes_on_demand_hourly"] * (1 - plan["discount"])


def _check(db: SessionDep, org_id: uuid.UUID, x: Any) -> None:
    from ccradar.costs.purchases import violations

    problems = violations(
        db, org_id, x, steady_hourly=_steady_commit_hourly(db, org_id, x.currency)
    )
    if problems:
        raise AppError(
            "Blocked by the purchase policy: " + " ".join(problems),
            status=422,
            code="policy_violation",
        )


@router.get("/purchases", response_model=list[PurchaseOut])
def list_purchases(auth: Reader, db: SessionDep) -> list[PurchaseOut]:
    from ccradar.models import PlannedPurchase

    return [
        _purchase_out(x)
        for x in db.scalars(select(PlannedPurchase).order_by(PlannedPurchase.planned_for))
    ]


@router.post("/purchases", response_model=PurchaseOut, status_code=201)
def plan_purchase(body: PurchaseIn, auth: Writer, db: SessionDep) -> PurchaseOut:
    """Plan a purchase. It is checked against the policy and previewed before anyone buys."""
    from ccradar.core.notify import Message, notify
    from ccradar.models import Organization, PlannedPurchase

    assert auth.org_id is not None  # noqa: S101
    org = db.get(Organization, auth.org_id)
    x = PlannedPurchase(
        org_id=auth.org_id,
        created_by_id=auth.user.id,
        status="planned",
        currency=org.default_currency if org else "USD",
        verification=[],
        **body.model_dump(),
    )
    _check(db, auth.org_id, x)
    db.add(x)
    db.flush()
    notify(
        db,
        "purchase_planned",
        [
            Message(
                title=f"Planned purchase: {x.description}",
                text=f"Planned for {x.planned_for:%d %b %Y}, {x.term} "
                f"{x.payment.replace('_', ' ')}, about {x.monthly_cost:,.2f} {x.currency} a "
                "month. Review and approve it in CloudCostRadar before then.",
                link="/savings/commitments",
                severity="info",
            )
        ],
    )
    return _purchase_out(x)


def _get_purchase(db: SessionDep, purchase_id: uuid.UUID) -> Any:
    from ccradar.models import PlannedPurchase

    x = db.get(PlannedPurchase, purchase_id)
    if x is None:
        raise NotFoundError("Planned purchase not found.")
    return x


@router.post("/purchases/{purchase_id}/approve", response_model=PurchaseOut)
def approve_purchase(purchase_id: uuid.UUID, auth: Settings, db: SessionDep) -> PurchaseOut:
    assert auth.org_id is not None  # noqa: S101
    x = _get_purchase(db, purchase_id)
    if x.status != "planned":
        raise AppError("Only planned purchases can be approved.", code="bad_status")
    _check(db, auth.org_id, x)  # the policy may have changed since it was planned
    x.status, x.approved_by_id = "approved", auth.user.id
    db.flush()
    return _purchase_out(x)


@router.post("/purchases/{purchase_id}/cancel", response_model=PurchaseOut)
def cancel_purchase(purchase_id: uuid.UUID, auth: Writer, db: SessionDep) -> PurchaseOut:
    x = _get_purchase(db, purchase_id)
    if x.status not in ("planned", "approved"):
        raise AppError("It is already bought or cancelled.", code="bad_status")
    x.status = "cancelled"
    db.flush()
    return _purchase_out(x)


@router.post("/purchases/{purchase_id}/purchased", response_model=PurchaseOut)
def mark_purchased(
    purchase_id: uuid.UUID, body: Bought, auth: Settings, db: SessionDep
) -> PurchaseOut:
    """Record that a person bought it in the AWS console (savings are then verified monthly)."""
    x = _get_purchase(db, purchase_id)
    if x.status != "approved":
        raise AppError("Approve the purchase first.", code="bad_status")
    if body.purchased_on < x.planned_for:
        raise AppError("The preview period is not over yet.", code="preview_not_over")
    x.status, x.commitment_id, x.purchased_on = "purchased", body.commitment_id, body.purchased_on
    db.flush()
    return _purchase_out(x)


@router.post("/purchases/verify")
def verify_purchases(auth: Settings, db: SessionDep) -> dict[str, int]:
    from ccradar.costs.purchases import verify
    from ccradar.models import Organization

    assert auth.org_id is not None  # noqa: S101
    org = db.get(Organization, auth.org_id)
    return {"verified": verify(db, auth.org_id, org.default_currency if org else "USD")}
