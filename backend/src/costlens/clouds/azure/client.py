"""A small Azure REST client: client-credentials sign-in and JSON calls.

Only Microsoft's own hosts are ever called (sign-in, Resource Manager, and the storage
account the customer named, which must be ``<name>.blob.core.windows.net``), so no
customer-supplied address reaches the network. Tokens are cached until shortly before
they expire. Every call is read-only."""

from __future__ import annotations

import re
import time
from typing import Any

import httpx

LOGIN = "https://login.microsoftonline.com"
ARM = "https://management.azure.com"
ARM_SCOPE = "https://management.azure.com/.default"
STORAGE_SCOPE = "https://storage.azure.com/.default"
GUID = re.compile(r"^[0-9a-fA-F-]{36}$")
STORAGE_ACCOUNT = re.compile(r"^[a-z0-9]{3,24}$")
TIMEOUT = 60.0


class AzureError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class AzureClient:
    def __init__(self, http: httpx.Client, tenant_id: str, client_id: str, secret: str) -> None:
        if not GUID.match(tenant_id) or not GUID.match(client_id):
            raise AzureError("tenant and client IDs must be GUIDs")
        self._http, self._tenant, self._client, self._secret = http, tenant_id, client_id, secret
        self._tokens: dict[str, tuple[str, float]] = {}

    def token(self, scope: str = ARM_SCOPE) -> str:
        cached = self._tokens.get(scope)
        if cached and cached[1] > time.time() + 60:
            return cached[0]
        try:
            r = self._http.post(
                f"{LOGIN}/{self._tenant}/oauth2/v2.0/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client,
                    "client_secret": self._secret,
                    "scope": scope,
                },
                timeout=TIMEOUT,
            )
        except httpx.HTTPError as exc:
            raise AzureError(f"could not reach Microsoft sign-in ({type(exc).__name__})") from exc
        if r.status_code != 200:
            code = (
                r.json().get("error")
                if r.headers.get("content-type", "").startswith("application/json")
                else None
            )
            raise AzureError(f"sign-in failed: {code or r.status_code}", r.status_code)
        body = r.json()
        self._tokens[scope] = (
            body["access_token"],
            time.time() + float(body.get("expires_in", 3600)),
        )
        return body["access_token"]

    def request(self, method: str, path_or_url: str, **kw: Any) -> httpx.Response:
        url = path_or_url if path_or_url.startswith("https://") else f"{ARM}{path_or_url}"
        if not url.startswith(f"{ARM}/"):
            raise AzureError("only Azure Resource Manager addresses are allowed here")
        headers = {"Authorization": f"Bearer {self.token()}", **kw.pop("headers", {})}
        for attempt in range(4):
            try:
                r = self._http.request(method, url, headers=headers, timeout=TIMEOUT, **kw)
            except httpx.HTTPError as exc:
                raise AzureError(f"could not reach Azure ({type(exc).__name__})") from exc
            if r.status_code == 429 and attempt < 3:  # Cost Management throttles hard
                time.sleep(
                    min(float(r.headers.get("retry-after", "5")), 30) if not self._fast else 0
                )
                continue
            if r.status_code >= 400:
                try:
                    err = r.json().get("error", {})
                    msg = f"{err.get('code')}: {err.get('message', '')[:300]}"
                except ValueError:
                    msg = r.text[:300]
                raise AzureError(msg, r.status_code)
            return r
        raise AzureError("Azure kept throttling the request", 429)

    _fast = False  # tests turn off the back-off sleep

    def blob(self, account: str, container: str, path: str = "", **params: str) -> httpx.Response:
        if not STORAGE_ACCOUNT.match(account):
            raise AzureError("not a storage account name")
        url = f"https://{account}.blob.core.windows.net/{container}{'/' + path if path else ''}"
        try:
            r = self._http.get(
                url,
                params=params or None,
                headers={
                    "Authorization": f"Bearer {self.token(STORAGE_SCOPE)}",
                    "x-ms-version": "2023-11-03",
                },
                timeout=TIMEOUT,
            )
        except httpx.HTTPError as exc:
            raise AzureError(f"could not reach storage ({type(exc).__name__})") from exc
        if r.status_code >= 400:
            raise AzureError(f"storage answered {r.status_code}", r.status_code)
        return r


def for_connection(conn: object, http: httpx.Client | None = None) -> AzureClient:
    """A client for a stored Azure connection (uses the app's HTTP client by default)."""
    from costlens.core.notify import outbound

    settings = getattr(conn, "settings", None) or {}
    return AzureClient(
        http or outbound()[0],
        settings.get("tenant_id", ""),
        settings.get("client_id", ""),
        getattr(conn, "secret", None) or "",
    )
