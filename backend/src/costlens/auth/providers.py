"""Configured identity providers (Google now; Entra, Okta and others in 1.5)."""

from __future__ import annotations

from dataclasses import dataclass, field

from costlens.config import Settings

GOOGLE_ISSUER = "https://accounts.google.com"


@dataclass(frozen=True)
class ProviderConfig:
    id: str  # stable key stored with identities, e.g. "google"
    display_name: str
    issuer: str
    client_id: str
    client_secret: str
    scopes: tuple[str, ...] = ("openid", "email", "profile")
    # Some IdPs put a second spelling of the issuer in tokens (Google: no scheme).
    extra_issuers: tuple[str, ...] = ()
    extra_auth_params: dict[str, str] = field(default_factory=dict)

    @property
    def issuers(self) -> tuple[str, ...]:
        return (self.issuer, *self.extra_issuers)


def configured_providers(settings: Settings) -> dict[str, ProviderConfig]:
    providers: dict[str, ProviderConfig] = {}
    if settings.google_client_id and settings.google_client_secret:
        providers["google"] = ProviderConfig(
            id="google",
            display_name="Google",
            issuer=GOOGLE_ISSUER,
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret.get_secret_value(),
            extra_issuers=("accounts.google.com",),
            extra_auth_params={"prompt": "select_account"},
        )
    return providers
