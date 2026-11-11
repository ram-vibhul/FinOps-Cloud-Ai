"""Savings findings: one ranked list across every detector."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from ccradar.accounts.roles import Permission, has_permission
from ccradar.api.deps import SessionDep, require
from ccradar.auth.web import AuthContext
from ccradar.core.errors import AppError, NotFoundError
from ccradar.db.base import utcnow
from ccradar.db.tenant import set_tenant
from ccradar.findings import service, workflow
from ccradar.findings.registry import load_all
from ccradar.models import Finding, FindingEvent, Membership, User

router = APIRouter(prefix="/api/v1/findings", tags=["findings"])
Reader = Annotated[AuthContext, require(Permission.READ)]
Writer = Annotated[AuthContext, require(Permission.WRITE)]


class FindingOut(BaseModel):
    id: uuid.UUID
    detector: str
    category: str
    title: str
    description: str
    provider: str | None
    account_id: str | None
    region: str | None
    service: str | None
    resource_type: str | None
    resource_id: str | None
    savings_monthly: float
    currency: str
    risk: str
    effort: str
    confidence: str = "medium"
    steps: list[str]
    commands: list[str]
    evidence: dict[str, Any]
    status: str
    status_note: str | None
    first_seen_at: datetime
    last_seen_at: datetime
    score: float = 0.0
    overlaps: bool = False  # another finding on the same resource saves more
    owner_id: uuid.UUID | None = None
    team: str | None = None
    owner_source: str | None = None
    approved_at: datetime | None = None
    applied_at: datetime | None = None
    verified_at: datetime | None = None
    realised_monthly: float | None = None
    measurement: dict[str, Any] = {}


@router.get("")
def list_findings(
    auth: Reader,
    db: SessionDep,
    status: Annotated[
        Literal["open", "approved", "applied", "verified", "dismissed", "gone", "active", "all"],
        Query(),
    ] = "open",
    category: Annotated[str | None, Query(max_length=30)] = None,
    team: Annotated[str | None, Query(max_length=200)] = None,
    owner: Annotated[str | None, Query(max_length=40)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> dict[str, Any]:
    q = select(Finding)
    if team:
        q = q.where(Finding.team.is_(None)) if team == "-" else q.where(Finding.team == team)
    if owner == "me":
        q = q.where(Finding.owner_id == auth.user.id)
    elif owner == "-":
        q = q.where(Finding.owner_id.is_(None))
    if status == "active":  # still being worked on
        q = q.where(Finding.status.in_(("open", "approved", "applied")))
    elif status != "all":
        q = q.where(Finding.status == status)
    if category:
        q = q.where(Finding.category == category)
    from ccradar.accounts.custom_roles import finding_visible

    rows = [f for f in db.scalars(q) if finding_visible(auth.scope, f)]
    best: dict[str, uuid.UUID] = {}
    for f in sorted(rows, key=lambda x: -float(x.savings_monthly)):
        best.setdefault(f.overlap_key, f.id)
    items = []
    for f in sorted(rows, key=service.score, reverse=True)[:limit]:
        out = FindingOut.model_validate(f, from_attributes=True)
        out.score = round(service.score(f), 2)
        out.overlaps = not service.stacked(f) and best[f.overlap_key] != f.id
        items.append(out)
    detectors = [
        {"name": d.name, "category": d.category, "title": d.title} for d in load_all().values()
    ]
    summary = service.summary(rows)
    verified = db.scalars(select(Finding).where(Finding.status == "verified")).all()
    summary["realised_monthly"] = round(sum(float(f.realised_monthly or 0) for f in verified), 2)
    summary["verified_count"] = len(verified)
    return {"summary": summary, "items": items, "detectors": detectors}


class FindingPatch(BaseModel):
    status: Literal["open", "approved", "applied", "verified", "dismissed"]
    note: str | None = Field(default=None, max_length=1000)


def _get(db: SessionDep, finding_id: uuid.UUID, auth: AuthContext | None = None) -> Finding:
    from ccradar.accounts.custom_roles import finding_visible

    f = db.get(Finding, finding_id)
    if f is None or (auth is not None and not finding_visible(auth.scope, f)):
        raise NotFoundError("Finding not found.")
    return f


def _out(f: Finding) -> FindingOut:
    out = FindingOut.model_validate(f, from_attributes=True)
    out.score = round(service.score(f), 2)
    return out


@router.patch("/{finding_id}", response_model=FindingOut)
def update_finding(
    finding_id: uuid.UUID, body: FindingPatch, auth: Writer, db: SessionDep
) -> FindingOut:
    """Approve (admins), mark applied, verify by hand (admins), dismiss or reopen."""
    f = _get(db, finding_id, auth)
    is_admin = has_permission(auth.role, Permission.MANAGE_SETTINGS) if auth.role else False
    workflow.set_status(db, f, body.status, auth.user.id, is_admin=is_admin, note=body.note)
    return _out(f)


class OwnerIn(BaseModel):
    owner_id: uuid.UUID | None


@router.put("/{finding_id}/owner", response_model=FindingOut)
def set_owner(finding_id: uuid.UUID, body: OwnerIn, auth: Writer, db: SessionDep) -> FindingOut:
    f = _get(db, finding_id, auth)
    if (
        body.owner_id is not None
        and db.scalar(
            select(Membership.user_id).where(
                Membership.org_id == auth.org_id, Membership.user_id == body.owner_id
            )
        )
        is None
    ):
        raise AppError("The owner must be a member of this organisation.", code="not_a_member")
    workflow.set_owner(db, f, body.owner_id, auth.user.id)
    return _out(f)


class CommentIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@router.post("/{finding_id}/comments", status_code=201)
def add_comment(
    finding_id: uuid.UUID, body: CommentIn, auth: Writer, db: SessionDep
) -> dict[str, Any]:
    f = _get(db, finding_id, auth)
    e = workflow.event(db, f, "comment", auth.user.id, body.text.strip())
    db.flush()
    return {"id": str(e.id), "at": e.at.isoformat()}


@router.get("/{finding_id}/history")
def history(finding_id: uuid.UUID, auth: Reader, db: SessionDep) -> list[dict[str, Any]]:
    f = _get(db, finding_id, auth)
    rows = db.execute(
        select(FindingEvent, User.name, User.email)
        .outerjoin(User, User.id == FindingEvent.user_id)
        .where(FindingEvent.finding_id == f.id)
        .order_by(FindingEvent.at)
    ).all()
    return [
        {
            "at": e.at.isoformat(),
            "kind": e.kind,
            "text": e.text,
            "data": e.data,
            "who": (name or email) if e.user_id else "CloudCostRadar",
        }
        for e, name, email in rows
    ]


@router.post("/{finding_id}/measure", response_model=FindingOut)
def measure_now(finding_id: uuid.UUID, auth: Writer, db: SessionDep) -> FindingOut:
    """Measure the realised saving now (applied findings; verification happens daily)."""
    f = _get(db, finding_id, auth)
    m = workflow.measure(db, f)
    if m is None:
        raise AppError(
            "Nothing to measure: mark it applied first, or this saving has no resource in "
            "the bill (verify it by hand).",
            code="not_measurable",
        )
    f.measurement, f.measured_at, f.realised_monthly = m, utcnow(), m["realised_monthly"]
    workflow.event(db, f, "measured", auth.user.id, data=m)
    db.flush()
    return _out(f)


class DismissIn(BaseModel):
    reason: Literal["not_applicable", "accepted_risk", "false_positive", "planned", "other"]
    scope: Literal["finding", "resource", "tag", "account", "check"] = "finding"
    tag: str | None = Field(default=None, max_length=300, pattern=r"^[^=]+=.+$")
    note: str = Field(default="", max_length=1000)
    expires_days: int | None = Field(default=None, ge=1, le=730)


@router.post("/{finding_id}/dismiss")
def dismiss(finding_id: uuid.UUID, body: DismissIn, auth: Writer, db: SessionDep) -> dict[str, Any]:
    """Dismiss with a reason; a wider scope (tag, account, whole check) is a rule (admins)."""
    from datetime import timedelta

    from ccradar.findings import suppressions
    from ccradar.models import Suppression

    f = _get(db, finding_id, auth)
    is_admin = has_permission(auth.role, Permission.MANAGE_SETTINGS) if auth.role else False
    if body.scope in suppressions.WIDE and not is_admin:
        raise AppError(
            "Only admins can dismiss a check for a tag, an account or everywhere.",
            status=403,
            code="admin_only",
        )
    if f.status not in ("open", "approved"):
        raise AppError(
            f"A finding that is {f.status} cannot be dismissed.", status=409, code="bad_transition"
        )
    value = {
        "finding": f.dedup_key,
        "resource": f.resource_id or "",
        "tag": body.tag or "",
        "account": f.account_id or "",
        "check": "",
    }[body.scope]
    if body.scope == "tag" and not body.tag:
        raise AppError("Choose the tag (key=value) to dismiss for.", code="tag_required")
    if body.scope in ("resource", "account") and not value:
        raise AppError(f"This finding has no {body.scope} to dismiss for.", code="no_scope")
    rule = Suppression(
        org_id=auth.org_id,
        detector="*" if body.scope == "resource" else f.detector,
        scope=body.scope,
        value=value,
        reason=body.reason,
        note=body.note.strip(),
        created_by_id=auth.user.id,
        expires_at=utcnow() + timedelta(days=body.expires_days) if body.expires_days else None,
    )
    db.add(rule)
    db.flush()
    return {"rule_id": str(rule.id), "dismissed": suppressions.apply(db)}


@router.get("/suppressions")
def list_suppressions(auth: Reader, db: SessionDep) -> list[dict[str, Any]]:
    from ccradar.findings.registry import load_all
    from ccradar.findings.suppressions import REASONS
    from ccradar.models import Suppression

    titles = {d.name: d.title for d in load_all().values()}
    covered: dict[str, int] = {}
    for f in db.scalars(select(Finding).where(Finding.status == "dismissed")):
        rid = (f.evidence or {}).get("suppressed_by")
        if rid:
            covered[rid] = covered.get(rid, 0) + 1
    return [
        {
            "id": str(r.id),
            "detector": r.detector,
            "check": "Every check" if r.detector == "*" else titles.get(r.detector, r.detector),
            "scope": r.scope,
            "value": r.value,
            "reason": r.reason,
            "reason_label": REASONS.get(r.reason, r.reason),
            "note": r.note,
            "expires_at": r.expires_at.isoformat() if r.expires_at else None,
            "created_at": r.created_at.isoformat(),
            "findings": covered.get(str(r.id), 0),
        }
        for r in db.scalars(select(Suppression).order_by(Suppression.created_at.desc()))
    ]


@router.delete("/suppressions/{rule_id}")
def delete_suppression(rule_id: uuid.UUID, auth: Writer, db: SessionDep) -> dict[str, int]:
    """Remove the rule; the findings it dismissed reopen."""
    from ccradar.findings import suppressions
    from ccradar.models import Suppression

    rule = db.get(Suppression, rule_id)
    if rule is None:
        raise NotFoundError("Rule not found.")
    is_admin = has_permission(auth.role, Permission.MANAGE_SETTINGS) if auth.role else False
    if rule.scope in suppressions.WIDE and not is_admin:
        raise AppError("Only admins can remove a wide rule.", status=403, code="admin_only")
    return {"reopened": suppressions.remove(db, rule, auth.user.id)}


class OwnerRuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    priority: int = Field(default=100, ge=1, le=10000)
    kind: Literal["account", "tag", "vtag", "service", "namespace", "category"]
    key: str = Field(default="", max_length=200)
    value: str = Field(min_length=1, max_length=500)
    team: str | None = Field(default=None, max_length=200)
    owner_id: uuid.UUID | None = None


def _rule_out(r: Any) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "name": r.name,
        "priority": r.priority,
        "kind": r.kind,
        "key": r.key,
        "value": r.value,
        "team": r.team,
        "owner_id": str(r.owner_id) if r.owner_id else None,
    }


def _check_rule(db: SessionDep, auth: AuthContext, body: OwnerRuleIn) -> None:
    if body.kind in ("tag", "vtag") and not body.key:
        raise AppError("Choose the tag key.", code="key_required")
    if not body.team and not body.owner_id:
        raise AppError("Choose a team, a person or both.", code="owner_required")
    if (
        body.owner_id
        and db.scalar(
            select(Membership.user_id).where(
                Membership.org_id == auth.org_id, Membership.user_id == body.owner_id
            )
        )
        is None
    ):
        raise AppError("The owner must be a member of this organisation.", code="not_a_member")


Settings = Annotated[AuthContext, require(Permission.MANAGE_SETTINGS)]


@router.get("/owner-rules")
def list_owner_rules(auth: Reader, db: SessionDep) -> list[dict[str, Any]]:
    from ccradar.findings.owners import preview
    from ccradar.models import OwnerRule

    rows = db.scalars(select(OwnerRule).order_by(OwnerRule.priority, OwnerRule.name))
    return [{**_rule_out(r), **preview(db, r)} for r in rows]


@router.post("/owner-rules", status_code=201)
def create_owner_rule(body: OwnerRuleIn, auth: Settings, db: SessionDep) -> dict[str, Any]:
    from ccradar.findings import owners
    from ccradar.models import OwnerRule

    _check_rule(db, auth, body)
    r = OwnerRule(org_id=auth.org_id, **body.model_dump())
    db.add(r)
    db.flush()
    owners.apply(db)
    return {**_rule_out(r), **owners.preview(db, r)}


@router.put("/owner-rules/{rule_id}")
def update_owner_rule(
    rule_id: uuid.UUID, body: OwnerRuleIn, auth: Settings, db: SessionDep
) -> dict[str, Any]:
    from ccradar.findings import owners
    from ccradar.models import OwnerRule

    r = db.get(OwnerRule, rule_id)
    if r is None:
        raise NotFoundError("Rule not found.")
    _check_rule(db, auth, body)
    for field, value in body.model_dump().items():
        setattr(r, field, value)
    db.flush()
    owners.apply(db)
    return {**_rule_out(r), **owners.preview(db, r)}


@router.delete("/owner-rules/{rule_id}")
def delete_owner_rule(rule_id: uuid.UUID, auth: Settings, db: SessionDep) -> dict[str, int]:
    from ccradar.findings import owners
    from ccradar.models import OwnerRule

    r = db.get(OwnerRule, rule_id)
    if r is None:
        raise NotFoundError("Rule not found.")
    db.delete(r)
    db.flush()
    return {"changed": owners.apply(db)}


@router.get("/plan")
def quick_wins(
    auth: Reader, db: SessionDep, team: Annotated[str | None, Query(max_length=200)] = None
) -> dict[str, Any]:
    """This week / this month / later, from savings, effort, risk and confidence."""
    from ccradar.findings.plan import build

    return build(db, team=team)


@router.get("/kept")
def kept(auth: Reader, db: SessionDep) -> dict[str, Any]:
    """Verified savings rechecked on the bill every month, and the ones that came back."""
    from ccradar.findings.kept import summary

    return summary(db)


@router.get("/upgrades")
def upgrades(auth: Reader, db: SessionDep) -> dict[str, Any]:
    """Cost of staying on old versions and generations, and what is coming up."""
    from ccradar.findings.upgrades import overview

    return overview(db)


@router.get("/noise")
def noise(auth: Reader, db: SessionDep) -> list[dict[str, Any]]:
    """Per check: dismissed vs acted on, so noisy checks can be tuned."""
    from ccradar.findings.suppressions import noise as stats

    return stats(db)


@router.get("/advice")
def advice_status(auth: Reader, db: SessionDep) -> dict[str, Any]:
    """What was imported from the clouds' own tools, per connection."""
    from ccradar.clouds.aws.advice import PROVIDER, SOURCES
    from ccradar.models import CloudConnection, ExternalRecommendation

    counts: dict[str, int] = {}
    for source in db.scalars(select(ExternalRecommendation.source)):
        counts[source] = counts.get(source, 0) + 1
    conns = list(
        db.scalars(
            select(CloudConnection).where(CloudConnection.provider.in_(("aws", "azure", "gcp")))
        )
    )
    clouds = {c.provider for c in conns}
    return {
        "sources": [
            {"source": k, "label": v, "count": counts.get(k, 0)}
            for k, v in SOURCES.items()
            if PROVIDER.get(k, "aws") in clouds or counts.get(k)
        ],
        "connections": [
            {
                "id": str(c.id),
                "name": c.name,
                "provider": c.provider,
                **((c.settings or {}).get("advice") or {}),
            }
            for c in conns
        ],
    }


@router.post("/advice/refresh")
def refresh_advice(auth: Writer, db: SessionDep, request: Request) -> dict[str, Any]:
    """Import AWS and Azure recommendations now (they also import daily), then merge them."""
    from ccradar.clouds.aws.advice import collect
    from ccradar.clouds.azure import advisor
    from ccradar.clouds.azure.client import for_connection
    from ccradar.clouds.gcp import client as gcp_client
    from ccradar.clouds.gcp import recommender
    from ccradar.models import CloudConnection

    assert auth.org_id is not None  # noqa: S101
    conns = list(
        db.scalars(
            select(CloudConnection).where(
                CloudConnection.provider.in_(("aws", "azure", "gcp")),
                CloudConnection.status == "active",
            )
        )
    )
    http = request.app.state.http
    results = []
    for c in conns:
        if c.provider == "azure":
            results.append(advisor.collect(db, for_connection(c, http), c.id))
        elif c.provider == "gcp":
            results.append(recommender.collect(db, gcp_client.for_connection(c, http), c.id))
        else:
            results.append(collect(db, request.app.state.aws, c.id))
    set_tenant(db, org_id=auth.org_id)
    service.run(db, auth.org_id, only=["aws.imported_advice"])
    return {
        "imported": sum(r.get("count", 0) for r in results),
        "errors": [r.get("errors") for r in results if r.get("errors")],
    }


@router.post("/run")
def run_now(auth: Writer, db: SessionDep) -> dict[str, Any]:
    """Run every detector for this organisation now (they also run daily)."""
    assert auth.org_id is not None  # noqa: S101
    return service.run(db, auth.org_id)
