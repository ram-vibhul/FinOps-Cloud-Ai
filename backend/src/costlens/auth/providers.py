"""Identity providers: instance-wide (environment) and per organisation (database).

Kinds:
* ``google`` - from COSTLENS_GOOGLE_CLIENT_ID/SECRET.
* ``entra``  - Microsoft Entra ID; ``tenant`` is a tenant id/domain, or
  ``organizations``/``common`` for multi-tenant apps (issuer checked per token).
* ``okta``   - ``domain`` like ``acme.okta.com`` (or an explicit ``issuer`` for a
  custom authorization server).
* ``oidc``   - any OpenID Connect provider by ``issuer``.

``trust_email``: treat the provider's email as verified when it does not say so.
Off by default because some IdPs (notably Entra) let users set arbitrary emails;
an unverified email never links to an existing account.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from costlens.config import Settings
from costlens.db.tenant import set_tenant

GOOGLE_ISSUER = "https://accounts.google.com"
ENTRA_MULTITENANT = frozenset({"common", "organizations", "consumers"})
ProviderKind = Literal["google", "entra", "okta", "oidc"]


@dataclass(frozen=True)
class ProviderConfig:
    id: str  # stable key stored with identities, e.g. "google", "entra", "org:<uuid>"
    display_name: str
    issuer: str
    client_id: str
    client_secret: str
    kind: ProviderKind = "oidc"
    scopes: tuple[str, ...] = ("openid", "email", "profile")
    extra_issuers: tuple[str, ...] = ()
    extra_auth_params: dict[str, str] = field(default_factory=dict)
    trust_email: bool = False
    entra_multitenant: bool = False
    customer_supplied: bool = False  # issuer came from a customer: SSRF-check it

    @property
    def issuers(self) -> tuple[str, ...]:
        return (self.issuer, *self.extra_issuers)


class ProviderSpec(BaseModel):
    """Input shape for an instance provider (env JSON) or an organisation provider (API)."""

    kind: Literal["entra", "okta", "oidc"]
    display_name: str = Field(min_length=1, max_length=100)
    client_id: str = Field(min_length=1, max_length=500)
    client_secret: str = Field(min_length=1, max_length=2000)
    issuer: str | None = Field(default=None, max_length=500)
    tenant: str | None = Field(default=None, max_length=200)
    domain: str | None = Field(default=None, max_length=253)
    trust_email: bool = False

    @model_validator(mode="after")
    def _resolve_issuer(self) -> ProviderSpec:
        self.issuer = resolve_issuer(self.kind, self.issuer, self.tenant, self.domain)
        return self


class InstanceProviderSpec(ProviderSpec):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,30}$")


def resolve_issuer(kind: str, issuer: str | None, tenant: str | None, domain: str | None) -> str:
    if kind == "entra":
        if not tenant:
            raise ValueError("Microsoft Entra needs a tenant (id, domain, or 'organizations')")
        return f"https://login.microsoftonline.com/{tenant}/v2.0"
    if kind == "okta" and not issuer:
        if not domain:
            raise ValueError("Okta needs a domain (e.g. acme.okta.com) or an issuer")
        return f"https://{domain.removeprefix('https://').rstrip('/')}"
    if not issuer:
        raise ValueError("an issuer URL is required")
    if not issuer.startswith("https://"):
        raise ValueError("the issuer must be an https:// URL")
    return issuer.rstrip("/")


def config_from_spec(spec: ProviderSpec, *, provider_id: str, customer: bool) -> ProviderConfig:
    assert spec.issuer is not None  # noqa: S101 - set by the validator
    tenant = (spec.tenant or "").lower()
    multitenant = spec.kind == "entra" and tenant in ENTRA_MULTITENANT
    return ProviderConfig(
        id=provider_id,
        display_name=spec.display_name,
        issuer=spec.issuer,
        client_id=spec.client_id,
        client_secret=spec.client_secret,
        kind=spec.kind,
        trust_email=spec.trust_email,
        entra_multitenant=multitenant,
        customer_supplied=customer,
    )


def parse_instance_providers(raw: str) -> list[InstanceProviderSpec]:
    if not raw.strip():
        return []
    try:
        data = json.loads(raw)
        return [InstanceProviderSpec.model_validate(item) for item in data]
    except (ValueError, ValidationError, TypeError) as exc:
        raise ValueError(f"COSTLENS_OIDC_PROVIDERS is not valid: {exc}") from exc


def configured_providers(settings: Settings) -> dict[str, ProviderConfig]:
    providers: dict[str, ProviderConfig] = {}
    if settings.google_client_id and settings.google_client_secret:
        providers["google"] = ProviderConfig(
            id="google",
            display_name="Google",
            issuer=GOOGLE_ISSUER,
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret.get_secret_value(),
            kind="google",
            extra_issuers=("accounts.google.com",),
            extra_auth_params={"prompt": "select_account"},
        )
    raw = settings.oidc_providers.get_secret_value() if settings.oidc_providers else ""
    for spec in parse_instance_providers(raw):
        providers[spec.id] = config_from_spec(spec, provider_id=spec.id, customer=False)
    return providers


ORG_PREFIX = "org:"


def org_provider(db: Session, provider_id: str) -> ProviderConfig | None:
    """Load an organisation's provider (``org:<uuid>``); reads across tenants on purpose."""
    from costlens.models import OrgIdentityProvider

    try:
        pid = uuid.UUID(provider_id.removeprefix(ORG_PREFIX))
    except ValueError:
        return None
    set_tenant(db, bypass=True)
    try:
        row = db.scalar(
            select(OrgIdentityProvider).where(
                OrgIdentityProvider.id == pid, OrgIdentityProvider.enabled.is_(True)
            )
        )
        if row is None:
            return None
        return row.to_config()
    finally:
        set_tenant(db)


def resolve_provider(db: Session, settings: Settings, provider_id: str) -> ProviderConfig | None:
    if provider_id.startswith(ORG_PREFIX):
        return org_provider(db, provider_id)
    return configured_providers(settings).get(provider_id)
