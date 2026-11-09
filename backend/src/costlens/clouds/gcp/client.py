"""A small Google Cloud REST client: service-account sign-in and JSON calls.

Signs a JWT with the service account's private key (RS256) and swaps it for an access
token. Only Google's own API hosts are called. Read-only access comes from the roles the
customer grants (BigQuery Data Viewer on the billing dataset, BigQuery Job User on the
project the queries run in, Recommender Viewer); the token scope does not widen them."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
import jwt

TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - an address, not a secret
SCOPE = "https://www.googleapis.com/auth/cloud-platform"
HOSTS = ("https://bigquery.googleapis.com/", "https://recommender.googleapis.com/")
TIMEOUT = 60.0


class GcpError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def parse_key(text: str) -> dict[str, Any]:
    """Check a service account key file and return it."""
    try:
        key = json.loads(text)
    except ValueError as exc:
        raise GcpError("the key is not valid JSON") from exc
    if not isinstance(key, dict) or key.get("type") != "service_account":
        raise GcpError("use a service account key (type service_account)")
    for field in ("client_email", "private_key", "project_id"):
        if not key.get(field):
            raise GcpError(f"the key has no {field}")
    return key


class GcpClient:
    def __init__(self, http: httpx.Client, key: dict[str, Any]) -> None:
        self._http, self._key = http, key
        self._token: tuple[str, float] | None = None

    @property
    def email(self) -> str:
        return str(self._key["client_email"])

    def token(self) -> str:
        if self._token and self._token[1] > time.time() + 60:
            return self._token[0]
        now = int(time.time())
        assertion = jwt.encode(
            {"iss": self.email, "scope": SCOPE, "aud": TOKEN_URL, "iat": now, "exp": now + 3600},
            self._key["private_key"],
            algorithm="RS256",
            headers={"kid": self._key.get("private_key_id", "")},
        )
        try:
            r = self._http.post(
                TOKEN_URL,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": assertion,
                },
                timeout=TIMEOUT,
            )
        except httpx.HTTPError as exc:
            raise GcpError(f"could not reach Google sign-in ({type(exc).__name__})") from exc
        if r.status_code != 200:
            try:
                detail = r.json().get("error_description") or r.json().get("error")
            except ValueError:
                detail = r.status_code
            raise GcpError(f"sign-in failed: {detail}", r.status_code)
        body = r.json()
        self._token = (body["access_token"], time.time() + float(body.get("expires_in", 3600)))
        return body["access_token"]

    def request(self, method: str, url: str, **kw: Any) -> dict[str, Any]:
        if not url.startswith(HOSTS):
            raise GcpError("only Google Cloud API addresses are allowed")
        try:
            r = self._http.request(
                method,
                url,
                headers={"Authorization": f"Bearer {self.token()}"},
                timeout=TIMEOUT,
                **kw,
            )
        except httpx.HTTPError as exc:
            raise GcpError(f"could not reach Google Cloud ({type(exc).__name__})") from exc
        if r.status_code >= 400:
            try:
                err = r.json().get("error", {})
                msg = f"{err.get('status') or r.status_code}: {str(err.get('message', ''))[:300]}"
            except ValueError:
                msg = r.text[:300]
            raise GcpError(msg, r.status_code)
        return r.json()


def for_connection(conn: object, http: httpx.Client | None = None) -> GcpClient:
    from costlens.core.notify import outbound

    return GcpClient(http or outbound()[0], parse_key(getattr(conn, "secret", None) or "{}"))
