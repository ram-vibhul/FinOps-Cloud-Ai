"""An in-memory OpenID Connect provider served through httpx.MockTransport."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

ISSUER = "https://accounts.google.com"
CLIENT_ID = "test-client.apps.googleusercontent.com"
CLIENT_SECRET = "test-secret"


def _new_key(kid: str) -> tuple[Any, dict[str, Any]]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update({"kid": kid, "use": "sig", "alg": "RS256"})
    return key, jwk


@dataclass
class FakeIdP:
    issuer: str = ISSUER
    client_id: str = CLIENT_ID
    client_secret: str = CLIENT_SECRET
    keys: dict[str, tuple[Any, dict[str, Any]]] = field(default_factory=dict)
    active_kid: str = "k1"
    codes: dict[str, dict[str, Any]] = field(default_factory=dict)
    requests: list[str] = field(default_factory=list)
    available: bool = True

    def __post_init__(self) -> None:
        self.keys[self.active_kid] = _new_key(self.active_kid)

    def rotate(self, kid: str) -> None:
        self.keys[kid] = _new_key(kid)
        self.active_kid = kid

    # --- browser side -------------------------------------------------------

    def authorize(self, authorization_url: str, **claims: Any) -> tuple[str, str]:
        """Simulate the user approving; returns (code, state) for the callback."""
        q = {k: v[0] for k, v in parse_qs(urlparse(authorization_url).query).items()}
        assert q["client_id"] == self.client_id
        assert q["code_challenge_method"] == "S256"
        code = secrets.token_urlsafe(16)
        base = {
            "sub": "google-sub-1",
            "email": "asha@example.com",
            "email_verified": True,
            "name": "Asha Raman",
            "picture": "https://example.com/a.png",
        }
        self.codes[code] = {
            "claims": {**base, **claims},
            "nonce": q["nonce"],
            "challenge": q["code_challenge"],
            "redirect_uri": q["redirect_uri"],
        }
        return code, q["state"]

    def id_token(
        self,
        claims: dict[str, Any],
        *,
        nonce: str,
        kid: str | None = None,
        alg: str = "RS256",
        key: Any = None,
    ) -> str:
        now = int(time.time())
        payload = {
            "iss": self.issuer,
            "aud": self.client_id,
            "iat": now,
            "exp": now + 300,
            "nonce": nonce,
            **claims,
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        kid = kid or self.active_kid
        signing_key = key if key is not None else self.keys[kid][0]
        return jwt.encode(payload, signing_key, algorithm=alg, headers={"kid": kid})

    # --- HTTP side ----------------------------------------------------------

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(f"{request.method} {request.url.path}")
        if not self.available:
            return httpx.Response(503)
        path = request.url.path
        if path == "/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": self.issuer,
                    "authorization_endpoint": f"{self.issuer}/o/oauth2/v2/auth",
                    "token_endpoint": "https://oauth2.googleapis.com/token",
                    "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs",
                    "id_token_signing_alg_values_supported": ["RS256"],
                },
            )
        if path == "/oauth2/v3/certs":
            return httpx.Response(200, json={"keys": [jwk for _, jwk in self.keys.values()]})
        if path == "/token" and request.method == "POST":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            grant = self.codes.pop(form.get("code", ""), None)  # single use
            if grant is None or form.get("client_secret") != self.client_secret:
                return httpx.Response(400, json={"error": "invalid_grant"})
            verifier = form.get("code_verifier", "")
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                .rstrip(b"=")
                .decode()
            )
            if challenge != grant["challenge"] or form.get("redirect_uri") != grant["redirect_uri"]:
                return httpx.Response(400, json={"error": "invalid_grant"})
            override = grant["claims"].pop("_id_token", None)
            token = (
                override(self, grant)
                if override
                else self.id_token(grant["claims"], nonce=grant["nonce"])
            )
            return httpx.Response(
                200, json={"id_token": token, "access_token": "x", "token_type": "Bearer"}
            )
        return httpx.Response(404)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))
