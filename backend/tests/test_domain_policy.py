from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select

from costlens.accounts.roles import Role
from costlens.config import Settings
from costlens.db import Database
from costlens.db.tenant import set_tenant
from costlens.jobs import InProcessRunner
from costlens.main import create_app
from costlens.models import Membership, OrgDomain, OrgIdentityProvider, User
from tests.fakes.oidc import CLIENT_ID, CLIENT_SECRET, FakeIdP
from tests.helpers import add_to_org, make_org, sign_in_client


@pytest.fixture
def idp() -> FakeIdP:
    return FakeIdP()


@pytest.fixture
def web(settings: Settings, db: Database, idp: FakeIdP) -> Iterator[TestClient]:
    settings.google_client_id = CLIENT_ID
    settings.google_client_secret = SecretStr(CLIENT_SECRET)
    app = create_app(settings, db=db, runner=InProcessRunner(), http_client=idp.client())
    with TestClient(app, follow_redirects=False) as c:
        yield c


def google(web: TestClient, idp: FakeIdP, email: str):
    r = web.get("/api/v1/auth/login/google")
    code, state = idp.authorize(
        r.headers["location"], email=email, email_verified=True, sub=f"sub-{email}"
    )
    return web.get("/api/v1/auth/callback/google", params={"code": code, "state": state})


def error_of(r) -> str | None:
    return parse_qs(urlparse(r.headers["location"]).query).get("error", [None])[0]


def verified_org(db: Database):
    org_id, owner = make_org(db, "Acme", "boss@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        s.add(OrgDomain(org_id=org_id, domain="acme.com", token="t", verified_at=datetime.now(UTC)))
    return org_id, owner


def test_policy_needs_verified_domain_and_sso(web: TestClient, db: Database) -> None:
    org_id, owner = make_org(db, "Acme", "boss@acme.com")
    c = sign_in_client(web, db, owner, org_id)
    body = {"enforce_sso": True, "auto_join": False}
    assert c.put("/api/v1/org/domain-policy", json=body).json()["code"] == "no_verified_domain"
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        s.add(OrgDomain(org_id=org_id, domain="acme.com", token="t", verified_at=datetime.now(UTC)))
    assert c.put("/api/v1/org/domain-policy", json=body).json()["code"] == "no_sso"  # no lock-out
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        s.add(
            OrgIdentityProvider(
                kind="oidc",
                display_name="Acme SSO",
                issuer="https://sso.acme.com",
                client_id="c",
                client_secret="s",
                trust_email=True,
                enabled=True,
            )
        )
    out = c.put(
        "/api/v1/org/domain-policy", json={**body, "auto_join": True, "auto_join_role": "member"}
    ).json()
    assert out == {"enforce_sso": True, "auto_join": True, "auto_join_role": "member"}
    assert (
        c.put("/api/v1/org/domain-policy", json={**body, "auto_join_role": "owner"}).status_code
        == 422
    )


def test_enforced_sso_blocks_other_sign_ins_except_owners(
    web: TestClient, db: Database, idp: FakeIdP
) -> None:
    org_id, _ = verified_org(db)
    add_to_org(db, org_id, "dev@acme.com", Role.MEMBER)
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        s.add(
            OrgIdentityProvider(
                kind="oidc",
                display_name="Acme SSO",
                issuer="https://sso.acme.com",
                client_id="c",
                client_secret="s",
                trust_email=True,
                enabled=True,
            )
        )
        from costlens.models import Organization

        org = s.get(Organization, org_id)
        assert org is not None
        org.settings = {**(org.settings or {}), "domain_policy": {"enforce_sso": True}}
    assert error_of(google(web, idp, "dev@acme.com")) == "sso_required"
    assert error_of(google(web, idp, "new@acme.com")) == "sso_required"
    assert error_of(google(web, idp, "boss@acme.com")) is None  # owners: break-glass
    assert error_of(google(web, idp, "someone@other.com")) is None  # other domains unaffected


def test_auto_join_with_default_role(web: TestClient, db: Database, idp: FakeIdP) -> None:
    org_id, _ = verified_org(db)
    with db.session() as s:
        set_tenant(s, bypass=True)
        from costlens.models import Organization

        org = s.get(Organization, org_id)
        assert org is not None
        org.settings = {
            **(org.settings or {}),
            "domain_policy": {"auto_join": True, "auto_join_role": "member"},
        }
    assert error_of(google(web, idp, "new@acme.com")) is None
    me = web.get("/api/v1/auth/me").json()
    assert [(o["name"], o["role"]) for o in me["organizations"]] == [("Acme", "member")]
    assert error_of(google(web, idp, "x@elsewhere.com")) is None
    assert web.get("/api/v1/auth/me").json()["organizations"] == []
    with db.session() as s:
        set_tenant(s, bypass=True)
        u = s.scalars(select(User).where(User.email == "new@acme.com")).one()
        assert (
            s.scalars(select(Membership).where(Membership.user_id == u.id)).one().role
            is Role.MEMBER
        )
