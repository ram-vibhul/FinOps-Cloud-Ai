from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ccradar.core.csrf import csrf_token
from ccradar.db import Database
from tests.helpers import make_org, sign_in_client

SPEC = {
    "kind": "oidc",
    "display_name": "X",
    "issuer": "https://sso.example.com",
    "client_id": "c",
    "client_secret": "s",
}


@pytest.fixture
def signed_in(client: TestClient, db: Database) -> TestClient:
    org_id, owner_id = make_org(db, "Acme", "o@acme.com")
    return sign_in_client(client, db, owner_id, org_id)


def test_valid_token_passes(signed_in: TestClient) -> None:
    assert signed_in.post("/api/v1/org/sso", json=SPEC).status_code == 201


def test_missing_token_rejected(signed_in: TestClient) -> None:
    del signed_in.headers["X-CSRF-Token"]
    r = signed_in.post("/api/v1/org/sso", json=SPEC)
    assert r.status_code == 403 and r.json()["code"] == "csrf_failed"
    assert signed_in.get("/api/v1/org/sso").status_code == 200  # safe methods unaffected


def test_wrong_token_rejected(signed_in: TestClient) -> None:
    signed_in.headers["X-CSRF-Token"] = csrf_token(signed_in.app.state.signing_key, "other")  # type: ignore[attr-defined]
    for method in ("post", "patch", "delete"):
        r = getattr(signed_in, method)("/api/v1/org/sso/00000000-0000-0000-0000-000000000000")
        assert r.status_code == 403


def test_cross_site_rejected_even_with_token(signed_in: TestClient) -> None:
    r = signed_in.post("/api/v1/org/sso", json=SPEC, headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    r = signed_in.post("/api/v1/org/sso", json=SPEC, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = signed_in.post("/api/v1/org/sso", json=SPEC, headers={"Origin": "null"})
    assert r.status_code == 403


def test_same_origin_allowed(signed_in: TestClient) -> None:
    r = signed_in.post(
        "/api/v1/org/sso",
        json=SPEC,
        headers={"Origin": "http://localhost:8000", "Sec-Fetch-Site": "same-origin"},
    )
    assert r.status_code == 201


def test_requests_without_session_cookie_are_not_csrf_checked(client: TestClient) -> None:
    # e.g. API keys / webhooks later; here simply unauthenticated -> 401, not 403.
    assert client.post("/api/v1/org/sso", json=SPEC).status_code == 401


def test_me_exposes_matching_token(signed_in: TestClient) -> None:
    assert (
        signed_in.get("/api/v1/auth/me").json()["csrf_token"] == signed_in.headers["X-CSRF-Token"]
    )


def test_any_localhost_port_allowed_in_local_mode_only(signed_in: TestClient) -> None:
    ok = signed_in.post("/api/v1/org/sso", json=SPEC, headers={"Origin": "http://localhost:5173"})
    assert ok.status_code == 201
    evil = signed_in.post(
        "/api/v1/org/sso", json=SPEC, headers={"Origin": "http://localhost.evil.com"}
    )
    assert evil.status_code == 403


def test_localhost_not_trusted_outside_local_mode() -> None:
    from ccradar.core.csrf import CSRFMiddleware

    async def app(scope, receive, send) -> None:
        return None

    mw = CSRFMiddleware(
        app,
        signing_key="k",
        session_cookie="s",
        allowed_origins=["https://app.ccradar.example"],
    )
    assert not mw._origin_allowed("http://localhost:5173")
    assert mw._origin_allowed("https://app.ccradar.example")


def test_stale_session_cookie_does_not_block_sign_in(client: TestClient, settings) -> None:
    settings.local_login = True
    client.cookies.set("ccradar_session", "stale-token-from-a-deleted-session")
    r = client.get("/api/v1/auth/me")
    assert r.status_code == 401
    assert "ccradar_session=;" in r.headers.get("set-cookie", "")  # browser told to drop it
    client.cookies.set("ccradar_session", "stale-token-from-a-deleted-session")
    assert client.post("/api/v1/auth/local", json={"email": "a@b.com"}).status_code == 204
    evil = client.post(
        "/api/v1/auth/local", json={"email": "a@b.com"}, headers={"Origin": "https://evil.example"}
    )
    assert evil.status_code == 403  # origin checks still apply to sign-in
