"""Entra, Okta and generic OIDC providers; organisation single sign-on."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import text

from ccradar.accounts import service as acc
from ccradar.accounts.roles import Role
from ccradar.auth.providers import configured_providers, parse_instance_providers
from ccradar.config import Settings
from ccradar.db import Database
from ccradar.db.tenant import set_tenant
from ccradar.jobs import InProcessRunner
from ccradar.main import create_app
from tests.conftest import public_resolver
from tests.fakes.oidc import FakeIdP
from tests.helpers import add_to_org, make_org, sign_in_client

TENANT = "3f8a1c2e-1111-4a5b-9c8d-0123456789ab"
ENTRA_COMMON = "https://login.microsoftonline.com/organizations/v2.0"
ENTRA_TENANT_ISS = f"https://login.microsoftonline.com/{TENANT}/v2.0"


def app_client(settings: Settings, db: Database, idp: FakeIdP) -> TestClient:
    app = create_app(
        settings,
        db=db,
        runner=InProcessRunner(),
        http_client=idp.client(),
        resolver=public_resolver,
    )
    return TestClient(app, follow_redirects=False)


def do_sign_in(c: TestClient, idp: FakeIdP, provider: str, **claims):
    r = c.get(f"/api/v1/auth/login/{provider}")
    assert r.status_code == 303, r.text
    if "error=" in r.headers["location"]:
        return r
    code, state = idp.authorize(r.headers["location"], **claims)
    return c.get(f"/api/v1/auth/callback/{provider}", params={"code": code, "state": state})


def err(r) -> str | None:
    return parse_qs(urlparse(r.headers["location"]).query).get("error", [None])[0]


# --- instance configuration -------------------------------------------------------


def test_parse_instance_providers() -> None:
    specs = parse_instance_providers(
        json.dumps(
            [
                {
                    "id": "entra",
                    "kind": "entra",
                    "display_name": "Microsoft",
                    "tenant": "organizations",
                    "client_id": "a",
                    "client_secret": "b",
                },
                {
                    "id": "okta",
                    "kind": "okta",
                    "display_name": "Okta",
                    "domain": "acme.okta.com",
                    "client_id": "a",
                    "client_secret": "b",
                },
                {
                    "id": "keycloak",
                    "kind": "oidc",
                    "display_name": "Keycloak",
                    "issuer": "https://sso.acme.com/realms/main/",
                    "client_id": "a",
                    "client_secret": "b",
                },
            ]
        )
    )
    assert [s.issuer for s in specs] == [
        ENTRA_COMMON,
        "https://acme.okta.com",
        "https://sso.acme.com/realms/main",
    ]


@pytest.mark.parametrize(
    "item",
    [
        {"id": "x", "kind": "entra", "display_name": "M", "client_id": "a", "client_secret": "b"},
        {
            "id": "x",
            "kind": "oidc",
            "display_name": "M",
            "issuer": "http://plain",
            "client_id": "a",
            "client_secret": "b",
        },
        {
            "id": "Bad Id",
            "kind": "okta",
            "display_name": "M",
            "domain": "a.okta.com",
            "client_id": "a",
            "client_secret": "b",
        },
    ],
)
def test_invalid_instance_providers(item: dict) -> None:
    with pytest.raises(ValueError, match="CCRADAR_OIDC_PROVIDERS"):
        parse_instance_providers(json.dumps([item]))


def test_instance_providers_listed(settings: Settings) -> None:
    settings.oidc_providers = SecretStr(
        json.dumps(
            [
                {
                    "id": "okta",
                    "kind": "okta",
                    "display_name": "Okta",
                    "domain": "acme.okta.com",
                    "client_id": "a",
                    "client_secret": "b",
                }
            ]
        )
    )
    assert list(configured_providers(settings)) == ["okta"]


# --- Microsoft Entra (multi-tenant) -----------------------------------------------


@pytest.fixture
def entra(settings: Settings, db: Database) -> Iterator[tuple[TestClient, FakeIdP]]:
    idp = FakeIdP(
        issuer=ENTRA_COMMON,
        client_id="entra-app",
        client_secret="entra-secret",
        discovery_issuer="https://login.microsoftonline.com/{tenantid}/v2.0",
        token_issuer=ENTRA_TENANT_ISS,
    )
    idp.default_claims = {
        "sub": "pairwise-sub",
        "tid": TENANT,
        "oid": "user-oid-1",
        "preferred_username": "ravi@contoso.com",
        "name": "Ravi",
    }
    settings.oidc_providers = SecretStr(
        json.dumps(
            [
                {
                    "id": "entra",
                    "kind": "entra",
                    "display_name": "Microsoft",
                    "tenant": "organizations",
                    "client_id": "entra-app",
                    "client_secret": "entra-secret",
                    "trust_email": True,
                }
            ]
        )
    )
    with app_client(settings, db, idp) as c:
        yield c, idp


def test_entra_multitenant_sign_in(entra, db: Database) -> None:
    c, idp = entra
    r = do_sign_in(c, idp, "entra")
    assert r.headers["location"] == "/", err(r)
    me = c.get("/api/v1/auth/me").json()
    assert me["user"]["email"] == "ravi@contoso.com"
    with db.session() as s:
        ident = s.execute(text("SELECT provider, subject FROM identities")).one()
    assert tuple(ident) == ("entra", f"{TENANT}:user-oid-1")


def test_entra_issuer_must_match_tenant(entra) -> None:
    c, idp = entra
    r = do_sign_in(c, idp, "entra", tid="99999999-1111-4a5b-9c8d-0123456789ab")
    assert err(r) == "invalid_token"


def test_entra_without_trust_cannot_link_existing_account(
    entra, settings: Settings, db: Database
) -> None:
    c, idp = entra
    settings.oidc_providers = SecretStr(
        json.dumps(
            [
                {
                    "id": "entra",
                    "kind": "entra",
                    "display_name": "Microsoft",
                    "tenant": "organizations",
                    "client_id": "entra-app",
                    "client_secret": "entra-secret",
                }
            ]
        )
    )
    idp.default_claims["email"] = "victim@contoso.com"
    with db.session() as s:
        acc.sign_in_with_identity(
            s, provider="google", subject="g1", email="victim@contoso.com", email_verified=True
        )
    assert err(do_sign_in(c, idp, "entra")) == "email_not_verified"


# --- Okta (instance) ------------------------------------------------------------


def test_okta_sign_in(settings: Settings, db: Database) -> None:
    idp = FakeIdP(issuer="https://acme.okta.com", client_id="okta-app", client_secret="okta-s")
    settings.oidc_providers = SecretStr(
        json.dumps(
            [
                {
                    "id": "okta",
                    "kind": "okta",
                    "display_name": "Okta",
                    "domain": "acme.okta.com",
                    "client_id": "okta-app",
                    "client_secret": "okta-s",
                }
            ]
        )
    )
    with app_client(settings, db, idp) as c:
        assert do_sign_in(c, idp, "okta", email="dev@acme.com").headers["location"] == "/"


# --- organisation single sign-on --------------------------------------------------


@pytest.fixture
def org_setup(settings: Settings, db: Database):
    org_id, owner_id = make_org(db, "Contoso", "owner@contoso.com")
    member_id = add_to_org(db, org_id, "member@contoso.com", Role.MEMBER)
    other_org, other_owner = make_org(db, "Other", "boss@other.com")
    idp = FakeIdP(issuer="https://sso.contoso.com", client_id="cid", client_secret="csecret")
    c = app_client(settings, db, idp)
    return {
        "c": c,
        "idp": idp,
        "org": org_id,
        "owner": owner_id,
        "member": member_id,
        "other_org": other_org,
        "other_owner": other_owner,
    }


SPEC = {
    "kind": "oidc",
    "display_name": "Contoso SSO",
    "issuer": "https://sso.contoso.com",
    "client_id": "cid",
    "client_secret": "csecret",
}


def test_admin_manages_org_provider(org_setup, db: Database) -> None:
    s = org_setup
    c = sign_in_client(s["c"], db, s["owner"], s["org"])
    created = c.post("/api/v1/org/sso", json=SPEC)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["has_client_secret"] is True and "client_secret" not in body
    assert body["login_url"] == f"/api/v1/auth/login/org:{body['id']}"
    assert len(c.get("/api/v1/org/sso").json()) == 1
    patched = c.patch(f"/api/v1/org/sso/{body['id']}", json={"display_name": "Contoso"})
    assert patched.json()["display_name"] == "Contoso"
    assert c.post(f"/api/v1/org/sso/{body['id']}/test").json() == {"ok": True, "error": None}
    assert c.delete(f"/api/v1/org/sso/{body['id']}").status_code == 204
    assert c.get("/api/v1/org/sso").json() == []


def test_member_cannot_manage_sso(org_setup, db: Database) -> None:
    s = org_setup
    c = sign_in_client(s["c"], db, s["member"], s["org"])
    assert c.get("/api/v1/org/sso").status_code == 403
    assert c.post("/api/v1/org/sso", json=SPEC).status_code == 403


def test_no_active_org_and_anonymous(org_setup, db: Database) -> None:
    s = org_setup
    assert s["c"].get("/api/v1/org/sso").status_code == 401
    uid = acc_user(db, "lonely@x.com")
    r = sign_in_client(s["c"], db, uid).get("/api/v1/org/sso")
    assert r.status_code == 409 and r.json()["code"] == "no_organization"


def acc_user(db: Database, email: str) -> uuid.UUID:
    from tests.helpers import make_user

    return make_user(db, email)


def test_other_org_cannot_see_or_touch_provider(org_setup, db: Database) -> None:
    s = org_setup
    c = sign_in_client(s["c"], db, s["owner"], s["org"])
    pid = c.post("/api/v1/org/sso", json=SPEC).json()["id"]
    c = sign_in_client(s["c"], db, s["other_owner"], s["other_org"])
    assert c.get("/api/v1/org/sso").json() == []
    assert c.patch(f"/api/v1/org/sso/{pid}", json={"enabled": False}).status_code == 404
    assert c.delete(f"/api/v1/org/sso/{pid}").status_code == 404


def test_sign_in_through_org_provider(org_setup, db: Database) -> None:
    s = org_setup
    c = sign_in_client(s["c"], db, s["owner"], s["org"])
    pid = c.post("/api/v1/org/sso", json=SPEC).json()["id"]
    c.cookies.clear()
    listed = c.get("/api/v1/auth/organizations/contoso/providers").json()
    assert listed == [{"id": f"org:{pid}", "name": "Contoso SSO"}]
    assert c.get("/api/v1/auth/organizations/nope/providers").json() == []
    r = do_sign_in(c, s["idp"], f"org:{pid}", email="member@contoso.com", sub="okta-77")
    assert r.headers["location"] == "/", err(r)
    me = c.get("/api/v1/auth/me").json()
    assert me["user"]["email"] == "member@contoso.com"
    assert me["organizations"][0]["slug"] == "contoso"


def test_disabled_or_unknown_org_provider(org_setup, db: Database) -> None:
    s = org_setup
    c = sign_in_client(s["c"], db, s["owner"], s["org"])
    pid = c.post("/api/v1/org/sso", json=SPEC).json()["id"]
    c.patch(f"/api/v1/org/sso/{pid}", json={"enabled": False})
    assert c.get(f"/api/v1/auth/login/org:{pid}").status_code == 404
    assert c.get(f"/api/v1/auth/login/org:{uuid.uuid4()}").status_code == 404
    assert c.get("/api/v1/auth/login/org:not-a-uuid").status_code == 404


def test_org_provider_cannot_target_internal_network(org_setup, db: Database) -> None:
    s = org_setup
    c = sign_in_client(s["c"], db, s["owner"], s["org"])
    spec = {**SPEC, "issuer": "https://metadata.internal"}
    pid = c.post("/api/v1/org/sso", json=spec).json()["id"]
    result = c.post(f"/api/v1/org/sso/{pid}/test").json()
    assert result["ok"] is False and "not a public" in result["error"]
    c.cookies.clear()
    assert err(c.get(f"/api/v1/auth/login/org:{pid}")) == "provider_misconfigured"
    assert not any("metadata" in r for r in s["idp"].requests)


def test_org_provider_validation(org_setup, db: Database) -> None:
    s = org_setup
    c = sign_in_client(s["c"], db, s["owner"], s["org"])
    assert c.post("/api/v1/org/sso", json={**SPEC, "issuer": "http://x.com"}).status_code == 422
    entra = {"kind": "entra", "display_name": "MS", "client_id": "a", "client_secret": "b"}
    assert c.post("/api/v1/org/sso", json=entra).status_code == 422
    ok = c.post("/api/v1/org/sso", json={**entra, "tenant": TENANT}).json()
    assert ok["issuer"] == ENTRA_TENANT_ISS


def test_org_provider_secret_encrypted_at_rest(org_setup, db: Database) -> None:
    s = org_setup
    c = sign_in_client(s["c"], db, s["owner"], s["org"])
    c.post("/api/v1/org/sso", json=SPEC)
    with db.session() as session:
        set_tenant(session, bypass=True)
        raw = str(
            session.execute(text("SELECT client_secret FROM org_identity_providers")).scalar()
        )
    assert "csecret" not in raw
