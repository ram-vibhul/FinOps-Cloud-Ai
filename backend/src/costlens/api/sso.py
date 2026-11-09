"""An organisation's own single sign-on providers (admins)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from costlens.accounts.roles import Permission
from costlens.api.deps import SessionDep, plan_feature, require
from costlens.audit.recorder import record
from costlens.auth.oidc import OIDCClient, OIDCError
from costlens.auth.providers import ProviderSpec, config_from_spec
from costlens.auth.web import AuthContext
from costlens.core.errors import AppError, NotFoundError
from costlens.models import OrgIdentityProvider

router = APIRouter(prefix="/api/v1/org/sso", tags=["single sign-on"])
Admin = Annotated[AuthContext, require(Permission.MANAGE_SETTINGS)]


class ProviderOut(BaseModel):
    id: uuid.UUID
    provider_id: str
    kind: str
    display_name: str
    issuer: str
    tenant: str | None
    client_id: str
    has_client_secret: bool
    trust_email: bool
    enabled: bool
    login_url: str


class ProviderPatch(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    client_secret: str | None = Field(default=None, min_length=1, max_length=2000)
    trust_email: bool | None = None
    enabled: bool | None = None


class TestResult(BaseModel):
    ok: bool
    error: str | None = None


def _out(row: OrgIdentityProvider) -> ProviderOut:
    return ProviderOut(
        id=row.id,
        provider_id=row.provider_id,
        kind=row.kind,
        display_name=row.display_name,
        issuer=row.issuer,
        tenant=row.tenant,
        client_id=row.client_id,
        has_client_secret=bool(row.client_secret),
        trust_email=row.trust_email,
        enabled=row.enabled,
        login_url=f"/api/v1/auth/login/{row.provider_id}",
    )


def _get(db: SessionDep, provider_id: uuid.UUID) -> OrgIdentityProvider:
    row = db.get(OrgIdentityProvider, provider_id)
    if row is None:
        raise NotFoundError("Sign-in provider not found.")
    return row


@router.get("", response_model=list[ProviderOut])
def list_providers(auth: Admin, db: SessionDep) -> list[ProviderOut]:
    rows = db.scalars(select(OrgIdentityProvider).order_by(OrgIdentityProvider.display_name))
    return [_out(r) for r in rows]


@router.post("", response_model=ProviderOut, status_code=201, dependencies=[plan_feature("sso")])
def create_provider(spec: ProviderSpec, auth: Admin, db: SessionDep) -> ProviderOut:
    if spec.kind not in ("entra", "okta", "oidc"):
        raise AppError("Unsupported provider kind.", code="invalid_kind")
    row = OrgIdentityProvider(
        kind=spec.kind,
        display_name=spec.display_name,
        issuer=spec.issuer or "",
        tenant=spec.tenant,
        client_id=spec.client_id,
        client_secret=spec.client_secret,
        trust_email=spec.trust_email,
        enabled=True,
    )
    db.add(row)
    db.flush()
    return _out(row)


@router.patch("/{provider_id}", response_model=ProviderOut, dependencies=[plan_feature("sso")])
def update_provider(
    provider_id: uuid.UUID, patch: ProviderPatch, auth: Admin, db: SessionDep
) -> ProviderOut:
    row = _get(db, provider_id)
    for field, value in patch.model_dump(exclude_none=True).items():
        setattr(row, field, value)
    db.flush()
    return _out(row)


@router.delete("/{provider_id}", status_code=204)
def delete_provider(provider_id: uuid.UUID, auth: Admin, db: SessionDep) -> Response:
    db.delete(_get(db, provider_id))
    db.flush()
    return Response(status_code=204)


@router.post("/{provider_id}/test", response_model=TestResult)
def test_provider(
    provider_id: uuid.UUID, request: Request, auth: Admin, db: SessionDep
) -> TestResult:
    """Check the issuer is reachable and publishes valid OpenID Connect metadata."""
    row = _get(db, provider_id)
    client = OIDCClient(row.to_config(), request.app.state.http, request.app.state.resolver)
    result = TestResult(ok=True)
    try:
        client.metadata()
        client._jwks_doc()
    except OIDCError as exc:
        result = TestResult(ok=False, error=str(exc))
    outcome = "ok" if result.ok else "failed"
    record(
        db,
        org_id=row.org_id,
        action="org_identity_provider.tested",
        target_type="org_identity_provider",
        target_id=row.id,
        summary=f"sign-in provider tested: {row.display_name} ({outcome})",
    )
    return result


__all__ = ["config_from_spec", "router"]
