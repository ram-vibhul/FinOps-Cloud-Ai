from __future__ import annotations

from collections.abc import Iterator
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from costlens.accounts import service as acc
from costlens.auth.web import safe_return_to
from costlens.config import Settings
from costlens.core.signing import BadSignatureError, Signer
from costlens.db import Database
from costlens.jobs import InProcessRunner
from costlens.main import create_app
from tests.fakes.oidc import CLIENT_ID, CLIENT_SECRET, FakeIdP


@pytest.fixture
def idp() -> FakeIdP:
    return FakeIdP()


@pytest.fixture
def auth_settings(settings: Settings) -> Settings:
    settings.google_client_id = CLIENT_ID
    settings.google_client_secret = SecretStr(CLIENT_SECRET)
    return settings


@pytest.fixture
def web(auth_settings: Settings, db: Database, idp: FakeIdP) -> Iterator[TestClient]:
    app = create_app(auth_settings, db=db, runner=InProcessRunner(), http_client=idp.client())
    with TestClient(app, follow_redirects=False) as c:
        yield c


def start(web: TestClient, return_to: str | None = None) -> str:
    params = {"return_to": return_to} if return_to else {}
    r = web.get("/api/v1/auth/login/google", params=params)
    assert r.status_code == 303
    assert "costlens_oidc" in r.cookies
    return r.headers["location"]


def sign_in(web: TestClient, idp: FakeIdP, return_to: str | None = None, **claims):
    code, state = idp.authorize(start(web, return_to), **claims)
    return web.get("/api/v1/auth/callback/google", params={"code": code, "state": state})


def logout(web: TestClient):
    return web.post(
        "/api/v1/auth/logout", headers={"X-CSRF-Token": web.cookies.get("costlens_csrf") or ""}
    )


def error_of(response) -> str | None:
    assert response.status_code == 303, response.text
    q = parse_qs(urlparse(response.headers["location"]).query)
    return q.get("error", [None])[0]


# --- happy path ---------------------------------------------------------------


def test_providers_listed_only_when_configured(web: TestClient) -> None:
    from costlens.auth.providers import configured_providers

    assert configured_providers(Settings(_env_file=None)) == {}  # type: ignore[call-arg]
    assert web.get("/api/v1/auth/providers").json() == [{"id": "google", "name": "Google"}]


def test_login_redirects_with_pkce_state_and_nonce(web: TestClient) -> None:
    q = parse_qs(urlparse(start(web)).query)
    assert q["response_type"] == ["code"]
    assert q["scope"] == ["openid email profile"]
    assert q["code_challenge_method"] == ["S256"]
    assert q["redirect_uri"] == ["http://localhost:8000/api/v1/auth/callback/google"]
    assert len(q["state"][0]) > 20 and len(q["nonce"][0]) > 20


def test_full_sign_in_creates_user_and_session(web: TestClient, idp: FakeIdP, db: Database) -> None:
    r = sign_in(web, idp, return_to="/costs?range=30d")
    assert r.status_code == 303
    assert r.headers["location"] == "/costs?range=30d"
    cookie = r.headers["set-cookie"]
    assert "costlens_session=" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert web.cookies.get("costlens_csrf")
    me = web.get("/api/v1/auth/me").json()
    assert me["csrf_token"] == web.cookies.get("costlens_csrf")
    assert me["user"]["email"] == "asha@example.com"
    assert me["user"]["name"] == "Asha Raman"
    assert me["organizations"] == [] and me["active_organization_id"] is None


def test_me_lists_organizations_and_active_role(
    web: TestClient, idp: FakeIdP, db: Database
) -> None:
    with db.session() as s:
        u = acc.sign_in_with_identity(
            s,
            provider="google",
            subject="google-sub-1",
            email="asha@example.com",
            email_verified=True,
        )
        org = acc.create_organization(s, name="Acme", owner=u)
        org_id = org.id
    sign_in(web, idp)
    me = web.get("/api/v1/auth/me").json()
    assert me["active_organization_id"] == str(org_id)
    assert me["role"] == "owner"
    assert me["organizations"][0]["slug"] == "acme"


def test_logout_revokes_session(web: TestClient, idp: FakeIdP) -> None:
    sign_in(web, idp)
    assert web.get("/api/v1/auth/me").status_code == 200
    assert logout(web).status_code == 204
    r = web.get("/api/v1/auth/me")
    assert r.status_code == 401
    assert r.json()["code"] == "unauthorized"


def test_stolen_cookie_useless_after_logout(web: TestClient, idp: FakeIdP) -> None:
    sign_in(web, idp)
    token = web.cookies.get("costlens_session") or ""
    logout(web)
    web.cookies.clear()
    web.cookies.set("costlens_session", token)
    assert web.get("/api/v1/auth/me").status_code == 401


def test_unauthenticated_me(client: TestClient) -> None:
    assert client.get("/api/v1/auth/me").status_code == 401


# --- attacks and failures ---------------------------------------------------------


def test_state_mismatch(web: TestClient, idp: FakeIdP) -> None:
    code, _ = idp.authorize(start(web))
    r = web.get("/api/v1/auth/callback/google", params={"code": code, "state": "forged"})
    assert error_of(r) == "state_mismatch"


def test_missing_or_tampered_state_cookie(web: TestClient, idp: FakeIdP) -> None:
    code, state = idp.authorize(start(web))
    tampered = (web.cookies.get("costlens_oidc") or "") + "x"
    web.cookies.clear()
    web.cookies.set("costlens_oidc", tampered)
    r = web.get("/api/v1/auth/callback/google", params={"code": code, "state": state})
    assert error_of(r) == "state_expired"
    web.cookies.clear()
    assert (
        error_of(web.get("/api/v1/auth/callback/google", params={"code": code, "state": state}))
        == "state_expired"
    )


def test_code_cannot_be_replayed(web: TestClient, idp: FakeIdP) -> None:
    code, state = idp.authorize(start(web))
    cookie = web.cookies.get("costlens_oidc") or ""
    assert (
        web.get("/api/v1/auth/callback/google", params={"code": code, "state": state}).headers[
            "location"
        ]
        == "/"
    )
    web.cookies.clear()
    web.cookies.set("costlens_oidc", cookie)
    r = web.get("/api/v1/auth/callback/google", params={"code": code, "state": state})
    assert error_of(r) == "code_exchange_failed"


def test_provider_error_param(web: TestClient) -> None:
    start(web)
    r = web.get("/api/v1/auth/callback/google", params={"error": "access_denied"})
    assert error_of(r) == "access_denied"


def test_pkce_verifier_must_match(web: TestClient, idp: FakeIdP) -> None:
    code, state = idp.authorize(start(web))
    idp.codes[code]["challenge"] = "different"
    r = web.get("/api/v1/auth/callback/google", params={"code": code, "state": state})
    assert error_of(r) == "code_exchange_failed"


@pytest.mark.parametrize(
    "make_token",
    [
        pytest.param(lambda idp, g: idp.id_token(g["claims"], nonce="wrong"), id="nonce"),
        pytest.param(
            lambda idp, g: idp.id_token({**g["claims"], "aud": "other"}, nonce=g["nonce"]),
            id="audience",
        ),
        pytest.param(
            lambda idp, g: idp.id_token(
                {**g["claims"], "iss": "https://evil.example"}, nonce=g["nonce"]
            ),
            id="issuer",
        ),
        pytest.param(
            lambda idp, g: idp.id_token({**g["claims"], "exp": 1000, "iat": 900}, nonce=g["nonce"]),
            id="expired",
        ),
        pytest.param(
            lambda idp, g: idp.id_token(g["claims"], nonce=g["nonce"], alg="HS256", key="x" * 32),
            id="hs256",
        ),
        pytest.param(
            lambda idp, g: FakeIdP().id_token(g["claims"], nonce=g["nonce"]), id="foreign-key"
        ),
        pytest.param(
            lambda idp, g: idp.id_token(
                {**g["claims"], "aud": ["other", idp.client_id], "azp": "other"}, nonce=g["nonce"]
            ),
            id="azp",
        ),
        pytest.param(lambda idp, g: "not-a-jwt", id="garbage"),
    ],
)
def test_bad_id_tokens_rejected(web: TestClient, idp: FakeIdP, make_token) -> None:
    r = sign_in(web, idp, _id_token=make_token)
    assert error_of(r) == "invalid_token"
    assert web.get("/api/v1/auth/me").status_code == 401


def test_unsigned_token_rejected(web: TestClient, idp: FakeIdP) -> None:
    import jwt as pyjwt

    def none_alg(idp: FakeIdP, g: dict) -> str:
        return pyjwt.encode(
            {**g["claims"], "iss": idp.issuer, "aud": idp.client_id, "nonce": g["nonce"]},
            None,  # type: ignore[arg-type]  # deliberately unsigned
            algorithm="none",
        )

    assert error_of(sign_in(web, idp, _id_token=none_alg)) == "invalid_token"


def test_key_rotation_refreshes_jwks(web: TestClient, idp: FakeIdP) -> None:
    sign_in(web, idp)
    logout(web)
    idp.rotate("k2")
    # Bypass the 60 s refresh floor by expiring the cache.
    client = web.app.state.oidc_clients["google"]  # type: ignore[attr-defined]
    client._jwks = (client._jwks[0] - 120, client._jwks[1])
    r = sign_in(web, idp)
    assert r.headers["location"] == "/"
    assert sum(r.endswith("/keys") for r in idp.requests) == 2


def test_unverified_email_cannot_take_over(web: TestClient, idp: FakeIdP, db: Database) -> None:
    with db.session() as s:
        acc.sign_in_with_identity(
            s,
            provider="google",
            subject="real-owner",
            email="asha@example.com",
            email_verified=True,
        )
    r = sign_in(web, idp, sub="attacker", email_verified=False)
    assert error_of(r) == "email_not_verified"


def test_email_verified_as_string(web: TestClient, idp: FakeIdP, db: Database) -> None:
    with db.session() as s:
        acc.sign_in_with_identity(
            s, provider="google", subject="other", email="asha@example.com", email_verified=True
        )
    assert sign_in(web, idp, email_verified="true").headers["location"] == "/"


def test_missing_email_rejected(web: TestClient, idp: FakeIdP) -> None:
    assert error_of(sign_in(web, idp, email=None)) == "email_missing"


def test_allowed_email_domains(web: TestClient, idp: FakeIdP, auth_settings: Settings) -> None:
    auth_settings.allowed_email_domains = ["yugiverse.in"]
    assert error_of(sign_in(web, idp)) == "domain_not_allowed"
    assert sign_in(web, idp, email="dev@YUGIVERSE.in", sub="s2").headers["location"] == "/"


def test_provider_down(web: TestClient, idp: FakeIdP) -> None:
    idp.available = False
    assert error_of(web.get("/api/v1/auth/login/google")) == "provider_unavailable"


def test_unknown_provider(web: TestClient) -> None:
    assert web.get("/api/v1/auth/login/myspace").status_code == 404


def test_secure_cookies_on_https(auth_settings: Settings, db: Database, idp: FakeIdP) -> None:
    auth_settings.base_url = "https://app.costlens.example"
    app = create_app(auth_settings, db=db, runner=InProcessRunner(), http_client=idp.client())
    with TestClient(app, base_url="https://app.costlens.example", follow_redirects=False) as c:
        r = sign_in(c, idp)
        cookie = r.headers.get_list("set-cookie")
        session = next(h for h in cookie if "costlens_session" in h)
        assert session.startswith("__Host-costlens_session=")
        assert "Secure" in session and "Path=/" in session
        assert c.get("/api/v1/auth/me").status_code == 200


# --- helpers ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "/"),
        ("/costs", "/costs"),
        ("//evil.com", "/"),
        ("/\\evil.com", "/"),
        ("https://evil.com", "/"),
        ("/ok\r\nSet-Cookie: x", "/"),
    ],
)
def test_safe_return_to(value: str | None, expected: str) -> None:
    assert safe_return_to(value) == expected


def test_signer() -> None:
    signer = Signer("secret", "oidc-state")
    token = signer.dumps({"a": 1}, max_age=60, now=1000)
    assert signer.loads(token, now=1030) == {"a": 1}
    with pytest.raises(BadSignatureError, match="expired"):
        signer.loads(token, now=1061)
    with pytest.raises(BadSignatureError):
        signer.loads(token[:-2] + "aa", now=1000)
    with pytest.raises(BadSignatureError):
        Signer("secret", "other-purpose").loads(token, now=1000)
    with pytest.raises(BadSignatureError):
        signer.loads("garbage", now=1000)


def test_local_login_only_when_enabled(client: TestClient, settings: Settings) -> None:
    assert client.post("/api/v1/auth/local", json={"email": "dev@x.com"}).status_code == 404
    settings.local_login = True
    assert {"id": "local", "name": "Local account (development)"} in client.get(
        "/api/v1/auth/providers"
    ).json()
    assert client.post("/api/v1/auth/local", json={"email": "Dev@X.com"}).status_code == 204
    assert client.get("/api/v1/auth/me").json()["user"]["email"] == "dev@x.com"


def test_local_login_refused_outside_local_mode() -> None:
    with pytest.raises(ValueError, match="only allowed in local mode"):
        Settings(
            _env_file=None,  # type: ignore[call-arg]
            deployment_mode="saas",
            local_login=True,
            secret_key="s",  # type: ignore[arg-type]
            encryption_keys="k",  # type: ignore[arg-type]
            database_url="postgresql://u@h/db",
        )
