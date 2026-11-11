"""OpenID Connect relying party: authorization code flow with PKCE.

* Discovery and JWKS documents are fetched with httpx and cached; an unknown
  signing key id triggers one JWKS refresh (rate-limited), so key rotation works.
* ID tokens are verified with PyJWT: asymmetric algorithms only, signature,
  issuer, audience, authorized party, expiry/issued-at (60 s leeway) and nonce.
"""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from ccradar.auth.providers import ProviderConfig
from ccradar.core.netguard import Resolver, UnsafeURLError, check_public_https_url, system_resolver

ALLOWED_ALGORITHMS = frozenset({"RS256", "RS384", "RS512", "PS256", "ES256", "ES384", "EdDSA"})
METADATA_TTL = 3600.0
JWKS_TTL = 3600.0
JWKS_MIN_REFRESH = 60.0
LEEWAY = 60
ENTRA_ISSUER = re.compile(r"^https://login\.microsoftonline\.com/([0-9a-f-]{36})/v2\.0$")


class OIDCError(Exception):
    """Sign-in failed. ``code`` is safe to show; details go to the log."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code


@dataclass(frozen=True)
class IdentityClaims:
    subject: str
    email: str
    email_verified: bool
    name: str
    picture: str | None
    raw: dict[str, Any]


def new_pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def _truthy(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.lower() == "true")


class OIDCClient:
    def __init__(
        self, provider: ProviderConfig, http: httpx.Client, resolver: Resolver = system_resolver
    ) -> None:
        self.provider = provider
        self.http = http
        self.resolver = resolver
        self._lock = threading.Lock()
        self._metadata: tuple[float, dict[str, Any]] | None = None
        self._jwks: tuple[float, dict[str, Any]] | None = None

    # --- documents ------------------------------------------------------

    def _guard(self, url: str) -> None:
        if self.provider.customer_supplied:
            try:
                check_public_https_url(url, self.resolver)
            except UnsafeURLError as exc:
                raise OIDCError("provider_misconfigured", str(exc)) from exc

    def _get_json(self, url: str) -> dict[str, Any]:
        self._guard(url)
        try:
            response = self.http.get(url, timeout=10.0)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise OIDCError("provider_unavailable", f"GET {url}: {exc}") from exc

    def metadata(self) -> dict[str, Any]:
        with self._lock:
            if self._metadata and time.monotonic() - self._metadata[0] < METADATA_TTL:
                return self._metadata[1]
        url = self.provider.issuer.rstrip("/") + "/.well-known/openid-configuration"
        doc = self._get_json(url)
        if self.provider.entra_multitenant:
            if not str(doc.get("issuer", "")).startswith("https://login.microsoftonline.com/"):
                raise OIDCError("provider_misconfigured", f"issuer mismatch: {doc.get('issuer')}")
        elif doc.get("issuer") not in self.provider.issuers:
            raise OIDCError("provider_misconfigured", f"issuer mismatch: {doc.get('issuer')}")
        with self._lock:
            self._metadata = (time.monotonic(), doc)
        return doc

    def _jwks_doc(self, *, force: bool = False) -> dict[str, Any]:
        with self._lock:
            cached = self._jwks
        age = time.monotonic() - cached[0] if cached else None
        fresh = age is not None and (age < JWKS_MIN_REFRESH or (age < JWKS_TTL and not force))
        if cached and fresh:
            return cached[1]
        doc = self._get_json(self.metadata()["jwks_uri"])
        with self._lock:
            self._jwks = (time.monotonic(), doc)
        return doc

    def _signing_key(self, kid: str | None) -> jwt.PyJWK:
        for force in (False, True):
            for jwk in self._jwks_doc(force=force).get("keys", []):
                if (kid is None or jwk.get("kid") == kid) and jwk.get("use", "sig") == "sig":
                    return jwt.PyJWK(jwk)
        raise OIDCError("invalid_token", f"no signing key for kid={kid}")

    # --- flow -------------------------------------------------------------

    def authorization_url(
        self, *, redirect_uri: str, state: str, nonce: str, code_challenge: str
    ) -> str:
        params = {
            "response_type": "code",
            "client_id": self.provider.client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(self.provider.scopes),
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            **self.provider.extra_auth_params,
        }
        return f"{self.metadata()['authorization_endpoint']}?{urlencode(params)}"

    def exchange_code(self, *, code: str, redirect_uri: str, code_verifier: str) -> dict[str, Any]:
        token_endpoint = self.metadata()["token_endpoint"]
        self._guard(token_endpoint)
        try:
            response = self.http.post(
                token_endpoint,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": self.provider.client_id,
                    "client_secret": self.provider.client_secret,
                    "code_verifier": code_verifier,
                },
                headers={"Accept": "application/json"},
                timeout=10.0,
            )
        except httpx.HTTPError as exc:
            raise OIDCError("provider_unavailable", str(exc)) from exc
        if response.status_code != 200:
            raise OIDCError(
                "code_exchange_failed", f"{response.status_code}: {response.text[:300]}"
            )
        body = response.json()
        if "id_token" not in body:
            raise OIDCError("code_exchange_failed", "no id_token in token response")
        return body

    def verify_id_token(self, token: str, *, nonce: str) -> IdentityClaims:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise OIDCError("invalid_token", str(exc)) from exc
        alg = header.get("alg")
        if alg not in ALLOWED_ALGORITHMS:
            raise OIDCError("invalid_token", f"algorithm not allowed: {alg}")
        key = self._signing_key(header.get("kid"))
        multitenant = self.provider.entra_multitenant
        try:
            claims = jwt.decode(
                token,
                key.key,
                algorithms=[alg],
                audience=self.provider.client_id,
                issuer=None if multitenant else list(self.provider.issuers),
                leeway=LEEWAY,
                options={
                    "require": ["exp", "iat", "iss", "aud", "sub"],
                    "verify_iss": not multitenant,
                },
            )
        except jwt.PyJWTError as exc:
            raise OIDCError("invalid_token", str(exc)) from exc
        if multitenant:
            # Multi-tenant Entra: the issuer must name the same tenant as the token's tid.
            match = ENTRA_ISSUER.match(str(claims.get("iss", "")))
            if not match or match.group(1) != str(claims.get("tid", "")).lower():
                raise OIDCError("invalid_token", "issuer does not match tenant")
        aud = claims.get("aud")
        if isinstance(aud, list) and len(aud) > 1 and claims.get("azp") != self.provider.client_id:
            raise OIDCError("invalid_token", "azp does not match client id")
        if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
            raise OIDCError("invalid_token", "nonce mismatch")
        email = claims.get("email")
        trust = self.provider.trust_email
        if not email and trust and "@" in str(claims.get("preferred_username", "")):
            email = claims["preferred_username"]  # Entra work accounts often omit `email`
        if not email:
            raise OIDCError("email_missing", "token has no email claim")
        verified = _truthy(claims.get("email_verified")) or (
            trust and "email_verified" not in claims
        )
        subject = str(claims["sub"])
        if self.provider.kind == "entra" and claims.get("tid") and claims.get("oid"):
            # Entra `sub` is per-application; tid+oid identifies the person stably.
            subject = f"{claims['tid']}:{claims['oid']}"
        return IdentityClaims(
            subject=subject,
            email=str(email),
            email_verified=verified,
            name=str(claims.get("name") or ""),
            picture=claims.get("picture"),
            raw=claims,
        )
