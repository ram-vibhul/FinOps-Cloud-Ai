"""SCIM 2.0 user provisioning (RFC 7643/7644) for identity providers such as Entra ID,
Okta and Google.

- ``/scim/v2/Users``: people in the organisation. Creating adds them (with the
  organisation's default SCIM role); ``active: false`` or DELETE removes them from the
  organisation (their account and other organisations are untouched).
- ``/scim/v2/Groups``: CostLens teams (8.2), with their members.

Authentication: a per-organisation SCIM token (``Authorization: Bearer scim_…``), made
by an admin in Settings, hashed, shown once. When the organisation has verified email
domains, only addresses in those domains can be provisioned. SCIM never touches owners:
it cannot create, change or remove an owner. Every change is audited as "SCIM".
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from costlens.accounts.roles import Permission, Role
from costlens.api.deps import SessionDep, plan_feature, require
from costlens.audit.recorder import Actor, set_actor
from costlens.auth.web import AuthContext
from costlens.core.errors import AppError
from costlens.db.base import utcnow
from costlens.db.tenant import set_tenant
from costlens.models import Membership, Organization, OrgDomain, Team, TeamMember, User

router = APIRouter(tags=["scim"])
Admin = Annotated[AuthContext, require(Permission.MANAGE_SETTINGS)]
USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
LIST = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
ERROR = "urn:ietf:params:scim:api:messages:2.0:Error"
PATCH_OP = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
MEDIA = "application/scim+json"
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,63}$")
FILTER = re.compile(r'^\s*(userName|displayName|externalId)\s+eq\s+"([^"]*)"\s*$', re.I)


class ScimError(Exception):
    def __init__(self, status: int, detail: str, scim_type: str | None = None) -> None:
        super().__init__(detail)
        self.status, self.detail, self.scim_type = status, detail, scim_type


def scim_error_response(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ScimError)  # noqa: S101
    body: dict[str, Any] = {"schemas": [ERROR], "status": str(exc.status), "detail": exc.detail}
    if exc.scim_type:
        body["scimType"] = exc.scim_type
    return JSONResponse(body, status_code=exc.status, media_type=MEDIA)


def _ok(body: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(body, status_code=status, media_type=MEDIA)


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def scim_org(
    db: SessionDep, request: Request, authorization: Annotated[str | None, Header()] = None
) -> Organization:
    token = (authorization or "").removeprefix("Bearer ").strip()
    if not token.startswith("scim_"):
        raise ScimError(401, "Missing or invalid SCIM token.")
    digest = _digest(token)
    set_tenant(db, bypass=True)
    for org in db.scalars(select(Organization).where(Organization.deletion_requested_at.is_(None))):
        cfg = (org.settings or {}).get("scim") or {}
        if cfg.get("token_hash") and hmac.compare_digest(cfg["token_hash"], digest):
            set_tenant(db, org_id=org.id)
            set_actor(
                db,
                Actor(
                    type="scim",
                    label="SCIM provisioning",
                    ip=request.client.host if request.client else None,
                ),
            )
            return org
    raise ScimError(401, "Missing or invalid SCIM token.")


ScimOrg = Annotated[Organization, Depends(scim_org)]


# --- settings (admins, in the app) ---------------------------------------------------


class ScimSettingsIn(BaseModel):
    default_role: Role = Role.MEMBER


@router.get("/api/v1/org/scim")
def scim_status(auth: Admin, db: SessionDep, request: Request) -> dict[str, Any]:
    org = db.get(Organization, auth.org_id)
    cfg = ((org.settings if org else None) or {}).get("scim") or {}
    return {
        "enabled": bool(cfg.get("token_hash")),
        "token_hint": cfg.get("token_hint"),
        "default_role": cfg.get("default_role", "member"),
        "base_url": f"{request.app.state.settings.base_url.rstrip('/')}/scim/v2",
    }


@router.post("/api/v1/org/scim", dependencies=[plan_feature("scim")])
def enable_scim(
    body: ScimSettingsIn, auth: Admin, db: SessionDep, request: Request
) -> dict[str, Any]:
    """Create (or replace) the SCIM token. The token is shown once."""
    if body.default_role is Role.OWNER:
        raise AppError("SCIM cannot make people owners.", code="bad_role")
    org = db.get(Organization, auth.org_id)
    assert org is not None  # noqa: S101
    token = "scim_" + secrets.token_urlsafe(32)
    org.settings = {
        **(org.settings or {}),
        "scim": {
            "token_hash": _digest(token),
            "token_hint": token[-6:],
            "default_role": body.default_role.value,
            "enabled_at": utcnow().isoformat(),
        },
    }
    db.flush()
    return {**scim_status(auth, db, request), "token": token}


@router.delete("/api/v1/org/scim", status_code=204)
def disable_scim(auth: Admin, db: SessionDep) -> None:
    org = db.get(Organization, auth.org_id)
    assert org is not None  # noqa: S101
    org.settings = {k: v for k, v in (org.settings or {}).items() if k != "scim"}
    db.flush()


# --- users ---------------------------------------------------------------------------


def _user_out(u: User, m: Membership | None, base: str) -> dict[str, Any]:
    given, _, family = (u.name or "").partition(" ")
    return {
        "schemas": [USER_SCHEMA],
        "id": str(u.id),
        "userName": u.email,
        "name": {"givenName": given, "familyName": family, "formatted": u.name or ""},
        "displayName": u.name or u.email,
        "emails": [{"value": u.email, "primary": True, "type": "work"}],
        "active": m is not None and u.is_active,
        "roles": [{"value": m.role.value}] if m else [],
        "meta": {"resourceType": "User", "location": f"{base}/Users/{u.id}"},
    }


def _base(request: Request) -> str:
    return f"{request.app.state.settings.base_url.rstrip('/')}/scim/v2"


def _members(db: Session, org: Organization) -> list[tuple[User, Membership]]:
    return list(
        db.execute(
            select(User, Membership)
            .join(Membership, Membership.user_id == User.id)
            .where(Membership.org_id == org.id)
            .order_by(User.email)
        ).all()
    )  # type: ignore[return-value]


def _check_email(db: Session, org: Organization, email: str) -> str:
    email = email.strip().lower()
    if not EMAIL.match(email):
        raise ScimError(400, "userName must be an email address.", "invalidValue")
    verified = set(db.scalars(select(OrgDomain.domain).where(OrgDomain.verified_at.is_not(None))))
    if verified and email.rsplit("@", 1)[1] not in verified:
        raise ScimError(403, "This email domain is not verified for the organisation.")
    return email


def _name(body: dict[str, Any]) -> str:
    n = body.get("name") or {}
    full = n.get("formatted") or " ".join(x for x in (n.get("givenName"), n.get("familyName")) if x)
    return str(full or body.get("displayName") or "")[:200]


def _page(request: Request, items: list[Any]) -> JSONResponse:
    start = max(int(request.query_params.get("startIndex", 1) or 1), 1)
    count = min(max(int(request.query_params.get("count", 100) or 100), 0), 500)
    chunk = items[start - 1 : start - 1 + count]
    return _ok(
        {
            "schemas": [LIST],
            "totalResults": len(items),
            "startIndex": start,
            "itemsPerPage": len(chunk),
            "Resources": chunk,
        }
    )


def _filter(request: Request) -> tuple[str, str] | None:
    raw = request.query_params.get("filter")
    if not raw:
        return None
    m = FILTER.match(raw)
    if not m:
        raise ScimError(
            400,
            "Only 'eq' filters on userName, displayName or externalId are supported.",
            "invalidFilter",
        )
    return m.group(1).lower(), m.group(2)


@router.get("/scim/v2/Users")
def list_users(org: ScimOrg, db: SessionDep, request: Request) -> JSONResponse:
    f = _filter(request)
    rows = _members(db, org)
    if f:
        attr, value = f
        rows = [
            (u, m)
            for u, m in rows
            if (u.email if attr == "username" else u.name if attr == "displayname" else None)
            == (value.lower() if attr == "username" else value)
        ]
    return _page(request, [_user_out(u, m, _base(request)) for u, m in rows])


def _member_or_404(db: Session, org: Organization, user_id: str) -> tuple[User, Membership | None]:
    try:
        uid = uuid.UUID(user_id)
    except ValueError as exc:
        raise ScimError(404, "User not found.") from exc
    m = db.scalar(select(Membership).where(Membership.org_id == org.id, Membership.user_id == uid))
    u = db.get(User, uid)
    if u is None or m is None:
        raise ScimError(404, "User not found.")
    return u, m


@router.get("/scim/v2/Users/{user_id}")
def get_user(user_id: str, org: ScimOrg, db: SessionDep, request: Request) -> JSONResponse:
    u, m = _member_or_404(db, org, user_id)
    return _ok(_user_out(u, m, _base(request)))


@router.post("/scim/v2/Users")
async def create_user(org: ScimOrg, db: SessionDep, request: Request) -> JSONResponse:
    body = await request.json()
    email = _check_email(db, org, str(body.get("userName") or ""))
    set_tenant(db, bypass=True)
    user = db.scalar(select(User).where(func.lower(User.email) == email))
    if user is None:
        user = User(email=email, name=_name(body))
        db.add(user)
        db.flush()
    set_tenant(db, org_id=org.id)
    if db.scalar(
        select(Membership).where(Membership.org_id == org.id, Membership.user_id == user.id)
    ):
        raise ScimError(409, "This person is already in the organisation.", "uniqueness")
    m = None
    if body.get("active", True):
        _room_for_member(db, org, request)
        role = Role(((org.settings or {}).get("scim") or {}).get("default_role", "member"))
        m = Membership(org_id=org.id, user_id=user.id, role=role)
        db.add(m)
        db.flush()
    return _ok(_user_out(user, m, _base(request)), 201)


def _room_for_member(db: Session, org: Organization, request: Request) -> None:
    """Plan limit on people (B.1), as a SCIM error the identity provider shows."""
    from costlens.billing.usage import enforce
    from costlens.core.errors import AppError

    try:
        enforce(db, org, request.app.state.settings, "members")
    except AppError as exc:
        raise ScimError(403, str(exc)) from exc


def _deactivate(db: Session, m: Membership) -> None:
    if m.role is Role.OWNER:
        raise ScimError(
            403, "SCIM cannot remove an owner; change their role in CostLens first.", "mutability"
        )
    db.delete(m)
    db.flush()


@router.put("/scim/v2/Users/{user_id}")
async def replace_user(
    user_id: str, org: ScimOrg, db: SessionDep, request: Request
) -> JSONResponse:
    body = await request.json()
    u, m = _member_or_404(db, org, user_id)
    if _name(body):
        set_tenant(db, bypass=True)
        u.name = _name(body)
        set_tenant(db, org_id=org.id)
    if body.get("active") is False and m is not None:
        _deactivate(db, m)
        m = None
    return _ok(_user_out(u, m, _base(request)))


@router.patch("/scim/v2/Users/{user_id}")
async def patch_user(user_id: str, org: ScimOrg, db: SessionDep, request: Request) -> JSONResponse:
    body = await request.json()
    u, m = _member_or_404(db, org, user_id)
    for op in body.get("Operations") or []:
        kind = str(op.get("op", "")).lower()
        path = str(op.get("path") or "").lower()
        value = op.get("value")
        if kind not in ("replace", "add"):
            continue
        updates = value if isinstance(value, dict) and not path else {path: value}
        for key, v in updates.items():
            k = key.lower()
            if k == "active" and (v is False or str(v).lower() == "false") and m is not None:
                _deactivate(db, m)
                m = None
            elif k in ("displayname", "name.formatted") and v:
                set_tenant(db, bypass=True)
                u.name = str(v)[:200]
                set_tenant(db, org_id=org.id)
    return _ok(_user_out(u, m, _base(request)))


@router.delete("/scim/v2/Users/{user_id}", status_code=204)
def delete_user(user_id: str, org: ScimOrg, db: SessionDep) -> None:
    _, m = _member_or_404(db, org, user_id)
    if m is not None:
        _deactivate(db, m)


# --- groups (teams) ------------------------------------------------------------------


def _group_out(db: Session, t: Team, base: str) -> dict[str, Any]:
    rows = db.execute(
        select(User.id, User.email)
        .join(TeamMember, TeamMember.user_id == User.id)
        .where(TeamMember.team_id == t.id)
    ).all()
    return {
        "schemas": [GROUP_SCHEMA],
        "id": str(t.id),
        "displayName": t.name,
        "members": [{"value": str(i), "display": e} for i, e in rows],
        "meta": {"resourceType": "Group", "location": f"{base}/Groups/{t.id}"},
    }


def _team(db: Session, group_id: str) -> Team:
    try:
        t = db.get(Team, uuid.UUID(group_id))
    except ValueError:
        t = None
    if t is None:
        raise ScimError(404, "Group not found.")
    return t


def _member_ids(db: Session, org: Organization, values: Any) -> list[uuid.UUID]:
    out = []
    allowed = set(db.scalars(select(Membership.user_id).where(Membership.org_id == org.id)))
    for v in values if isinstance(values, list) else [values]:
        try:
            uid = uuid.UUID(str(v.get("value") if isinstance(v, dict) else v))
        except ValueError:
            continue
        if uid in allowed:
            out.append(uid)
    return out


@router.get("/scim/v2/Groups")
def list_groups(org: ScimOrg, db: SessionDep, request: Request) -> JSONResponse:
    f = _filter(request)
    teams = list(db.scalars(select(Team).order_by(Team.name)))
    if f and f[0] == "displayname":
        teams = [t for t in teams if t.name == f[1]]
    return _page(request, [_group_out(db, t, _base(request)) for t in teams])


@router.get("/scim/v2/Groups/{group_id}")
def get_group(group_id: str, org: ScimOrg, db: SessionDep, request: Request) -> JSONResponse:
    return _ok(_group_out(db, _team(db, group_id), _base(request)))


@router.post("/scim/v2/Groups")
async def create_group(org: ScimOrg, db: SessionDep, request: Request) -> JSONResponse:
    body = await request.json()
    name = str(body.get("displayName") or "").strip()[:100]
    if not name:
        raise ScimError(400, "displayName is required.", "invalidValue")
    if db.scalar(select(Team.id).where(Team.name == name)):
        raise ScimError(409, "A team with this name exists.", "uniqueness")
    t = Team(org_id=org.id, name=name)
    db.add(t)
    db.flush()
    for uid in _member_ids(db, org, body.get("members") or []):
        db.add(TeamMember(org_id=org.id, team_id=t.id, user_id=uid))
    db.flush()
    return _ok(_group_out(db, t, _base(request)), 201)


@router.patch("/scim/v2/Groups/{group_id}")
async def patch_group(
    group_id: str, org: ScimOrg, db: SessionDep, request: Request
) -> JSONResponse:
    body = await request.json()
    t = _team(db, group_id)
    current = set(db.scalars(select(TeamMember.user_id).where(TeamMember.team_id == t.id)))
    for op in body.get("Operations") or []:
        kind = str(op.get("op", "")).lower()
        path = str(op.get("path") or "")
        value = op.get("value")
        if path.lower() == "displayname" or (isinstance(value, dict) and "displayName" in value):
            t.name = str(value["displayName"] if isinstance(value, dict) else value)[:100]
            continue
        if kind == "add":
            current |= set(_member_ids(db, org, value or []))
        elif kind == "replace" and path.lower() == "members":
            current = set(_member_ids(db, org, value or []))
        elif kind == "remove":
            m = re.match(r'members\[value eq "([^"]+)"\]', path)
            if m:
                current.discard(uuid.UUID(m.group(1)))
            elif path.lower() == "members":
                current -= set(_member_ids(db, org, value or [])) if value else current.copy()
    db.execute(delete(TeamMember).where(TeamMember.team_id == t.id))
    for uid in current:
        db.add(TeamMember(org_id=org.id, team_id=t.id, user_id=uid))
    db.flush()
    return _ok(_group_out(db, t, _base(request)))


@router.delete("/scim/v2/Groups/{group_id}", status_code=204)
def delete_group(group_id: str, org: ScimOrg, db: SessionDep) -> None:
    db.delete(_team(db, group_id))
    db.flush()


# --- discovery -----------------------------------------------------------------------


@router.get("/scim/v2/ServiceProviderConfig")
def service_provider_config(org: ScimOrg) -> JSONResponse:
    return _ok(
        {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
            "patch": {"supported": True},
            "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
            "filter": {"supported": True, "maxResults": 500},
            "changePassword": {"supported": False},
            "sort": {"supported": False},
            "etag": {"supported": False},
            "authenticationSchemes": [
                {
                    "type": "oauthbearertoken",
                    "name": "Bearer token",
                    "description": "Organisation SCIM token (scim_…).",
                }
            ],
        }
    )


@router.get("/scim/v2/ResourceTypes")
def resource_types(org: ScimOrg) -> JSONResponse:
    types = [
        {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"],
            "id": "User",
            "name": "User",
            "endpoint": "/Users",
            "schema": USER_SCHEMA,
        },
        {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"],
            "id": "Group",
            "name": "Group",
            "endpoint": "/Groups",
            "schema": GROUP_SCHEMA,
        },
    ]
    return _ok({"schemas": [LIST], "totalResults": 2, "Resources": types})
