"""Teams inside an organisation and verified email domains (8.2)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from ccradar.accounts import domains
from ccradar.accounts.roles import Permission
from ccradar.api.deps import SessionDep, require
from ccradar.auth.web import AuthContext
from ccradar.core.errors import AppError, NotFoundError
from ccradar.db.tenant import set_tenant
from ccradar.models import Membership, OrgDomain, Team, TeamMember, User

router = APIRouter(tags=["teams and domains"])
Reader = Annotated[AuthContext, require(Permission.READ)]
Admin = Annotated[AuthContext, require(Permission.MANAGE_MEMBERS)]


class TeamIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    cost_key: str | None = Field(default=None, max_length=200)
    description: str = Field(default="", max_length=500)
    members: list[uuid.UUID] = Field(default_factory=list, max_length=1000)


class TeamPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    cost_key: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    members: list[uuid.UUID] | None = Field(default=None, max_length=1000)


class DomainIn(BaseModel):
    domain: str = Field(max_length=253)


class DomainOut(BaseModel):
    id: uuid.UUID
    domain: str
    verified_at: datetime | None
    last_checked_at: datetime | None
    last_error: str | None
    record: dict[str, str]


def _team_out(db: SessionDep, t: Team) -> dict[str, Any]:
    rows = db.execute(
        select(User.id, User.email, User.name)
        .join(TeamMember, TeamMember.user_id == User.id)
        .where(TeamMember.team_id == t.id)
        .order_by(User.email)
    ).all()
    return {
        "id": str(t.id),
        "name": t.name,
        "cost_key": t.cost_key or t.name,
        "description": t.description,
        "members": [{"user_id": str(i), "email": e, "name": n} for i, e, n in rows],
    }


def _set_members(db: SessionDep, auth: AuthContext, t: Team, users: list[uuid.UUID]) -> None:
    allowed = set(db.scalars(select(Membership.user_id).where(Membership.org_id == auth.org_id)))
    unknown = [u for u in users if u not in allowed]
    if unknown:
        raise AppError("Only members of this organisation can join a team.", code="not_a_member")
    db.execute(delete(TeamMember).where(TeamMember.team_id == t.id))
    for u in dict.fromkeys(users):
        db.add(TeamMember(org_id=t.org_id, team_id=t.id, user_id=u))


def _team(db: SessionDep, team_id: uuid.UUID) -> Team:
    t = db.get(Team, team_id)
    if t is None:
        raise NotFoundError("Team not found.")
    return t


@router.get("/api/v1/org/teams")
def list_teams(auth: Reader, db: SessionDep) -> list[dict[str, Any]]:
    return [_team_out(db, t) for t in db.scalars(select(Team).order_by(Team.name))]


@router.post("/api/v1/org/teams", status_code=201)
def create_team(body: TeamIn, auth: Admin, db: SessionDep) -> dict[str, Any]:
    assert auth.org_id is not None  # noqa: S101
    name = body.name.strip()
    if db.scalar(select(Team.id).where(Team.name == name)):
        raise AppError("A team with this name already exists.", status=409, code="duplicate")
    t = Team(
        org_id=auth.org_id,
        name=name,
        cost_key=(body.cost_key or "").strip() or None,
        description=body.description,
    )
    db.add(t)
    db.flush()
    _set_members(db, auth, t, body.members)
    db.flush()
    return _team_out(db, t)


@router.patch("/api/v1/org/teams/{team_id}")
def update_team(team_id: uuid.UUID, body: TeamPatch, auth: Admin, db: SessionDep) -> dict[str, Any]:
    t = _team(db, team_id)
    if body.name is not None:
        t.name = body.name.strip()
    if body.cost_key is not None:
        t.cost_key = body.cost_key.strip() or None
    if body.description is not None:
        t.description = body.description
    if body.members is not None:
        _set_members(db, auth, t, body.members)
    db.flush()
    return _team_out(db, t)


@router.delete("/api/v1/org/teams/{team_id}", status_code=204)
def delete_team(team_id: uuid.UUID, auth: Admin, db: SessionDep) -> Response:
    db.delete(_team(db, team_id))
    return Response(status_code=204)


@router.get("/api/v1/me/teams")
def my_teams(auth: Reader, db: SessionDep) -> list[dict[str, Any]]:
    """The signed-in person's teams here (used to filter to "my team")."""
    teams = db.scalars(
        select(Team)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .where(TeamMember.user_id == auth.user.id)
    )
    return [{"id": str(t.id), "name": t.name, "cost_key": t.cost_key or t.name} for t in teams]


def _domain_out(d: OrgDomain) -> DomainOut:
    return DomainOut(
        id=d.id,
        domain=d.domain,
        verified_at=d.verified_at,
        last_checked_at=d.last_checked_at,
        last_error=d.last_error,
        record=domains.record(d),
    )


@router.get("/api/v1/org/domains", response_model=list[DomainOut])
def list_domains(auth: Admin, db: SessionDep) -> list[DomainOut]:
    return [_domain_out(d) for d in db.scalars(select(OrgDomain).order_by(OrgDomain.domain))]


@router.post("/api/v1/org/domains", response_model=DomainOut, status_code=201)
def add_domain(body: DomainIn, auth: Admin, db: SessionDep) -> DomainOut:
    assert auth.org_id is not None  # noqa: S101
    try:
        name = domains.normalise(body.domain)
    except ValueError as exc:
        raise AppError(f"{str(exc).capitalize()}.", code="bad_domain") from exc
    if db.scalar(select(OrgDomain.id).where(OrgDomain.domain == name)):
        raise AppError("This domain is already added.", status=409, code="duplicate")
    d = OrgDomain(org_id=auth.org_id, domain=name, token=domains.new_token())
    db.add(d)
    db.flush()
    return _domain_out(d)


@router.post("/api/v1/org/domains/{domain_id}/verify", response_model=DomainOut)
def verify_domain(domain_id: uuid.UUID, request: Request, auth: Admin, db: SessionDep) -> DomainOut:
    d = db.get(OrgDomain, domain_id)
    if d is None:
        raise NotFoundError("Domain not found.")
    # One organisation per verified domain (checked across organisations).
    set_tenant(db, bypass=True)
    taken = db.scalar(
        select(OrgDomain.id).where(
            OrgDomain.domain == d.domain,
            OrgDomain.verified_at.is_not(None),
            OrgDomain.org_id != d.org_id,
        )
    )
    set_tenant(db, org_id=auth.org_id, user_id=auth.user.id)
    if taken:
        raise AppError(
            "Another organisation has already verified this domain.",
            status=409,
            code="domain_taken",
        )
    domains.check(request.app.state.http, request.app.state.settings.dns_over_https_url, d)
    db.flush()
    return _domain_out(d)


@router.delete("/api/v1/org/domains/{domain_id}", status_code=204)
def remove_domain(domain_id: uuid.UUID, auth: Admin, db: SessionDep) -> Response:
    d = db.get(OrgDomain, domain_id)
    if d is None:
        raise NotFoundError("Domain not found.")
    db.delete(d)
    return Response(status_code=204)


class DomainPolicyIn(BaseModel):
    enforce_sso: bool = False
    auto_join: bool = False
    auto_join_role: str = Field(default="viewer", pattern="^(viewer|member|admin)$")


@router.get("/api/v1/org/domain-policy")
def get_domain_policy(auth: Admin, db: SessionDep) -> dict[str, Any]:
    from ccradar.accounts.domain_policy import policy_of
    from ccradar.models import Organization

    org = db.get(Organization, auth.org_id)
    assert org is not None  # noqa: S101
    return policy_of(org)


@router.put("/api/v1/org/domain-policy")
def set_domain_policy(body: DomainPolicyIn, auth: Admin, db: SessionDep) -> dict[str, Any]:
    from ccradar.accounts.domain_policy import policy_of, sso_providers
    from ccradar.models import Organization

    org = db.get(Organization, auth.org_id)
    assert org is not None  # noqa: S101
    if (body.enforce_sso or body.auto_join) and not db.scalar(
        select(OrgDomain.id).where(OrgDomain.verified_at.is_not(None))
    ):
        raise AppError("Verify a domain first.", code="no_verified_domain")
    if body.enforce_sso:
        providers = sso_providers(db, org)
        set_tenant(db, org_id=auth.org_id, user_id=auth.user.id)
        if not providers:
            raise AppError(
                "Add and turn on your organisation's own sign-in first, so nobody is locked out.",
                code="no_sso",
            )
    org.settings = {**(org.settings or {}), "domain_policy": body.model_dump()}
    db.flush()
    return policy_of(org)
