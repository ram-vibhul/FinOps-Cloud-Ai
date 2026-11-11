from __future__ import annotations

from datetime import timedelta
from email import message_from_bytes

import pytest
from fastapi.testclient import TestClient

from ccradar.accounts.roles import Role
from ccradar.config import Settings
from ccradar.core.email import Email, MemorySender, SmtpSender
from ccradar.db import Database
from ccradar.db.base import utcnow
from ccradar.db.tenant import set_tenant
from ccradar.jobs import InProcessRunner, load_all_jobs
from ccradar.main import create_app
from ccradar.models import Invitation
from tests.conftest import public_resolver
from tests.helpers import add_to_org, make_org, make_user, sign_in_client


@pytest.fixture
def env(settings: Settings, db: Database):
    load_all_jobs()
    outbox = MemorySender()
    runner = InProcessRunner()
    app = create_app(settings, db=db, runner=runner, resolver=public_resolver, email=outbox)
    org_id, owner = make_org(db, "Acme <Labs>", "owner@acme.com")
    admin = add_to_org(db, org_id, "admin@acme.com", Role.ADMIN)
    member = add_to_org(db, org_id, "member@acme.com", Role.MEMBER)
    with TestClient(app) as c:
        yield {
            "c": c,
            "db": db,
            "org": org_id,
            "owner": owner,
            "admin": admin,
            "member": member,
            "outbox": outbox,
            "runner": runner,
        }


def invite(env, actor="owner", email="new@x.com", role="member"):
    c = sign_in_client(env["c"], env["db"], env[actor], env["org"])
    return c.post("/api/v1/org/invitations", json={"email": email, "role": role})


def token_of(r) -> str:
    return r.json()["invite_url"].rsplit("/", 1)[-1]


def test_invite_sends_email_with_link(env) -> None:
    r = invite(env, email="New.Person@X.com")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["email"] == "new.person@x.com" and body["status"] == "pending"
    assert body["invite_url"].startswith("http://localhost:8000/invite/")
    [mail] = env["outbox"].outbox
    assert mail.to == "new.person@x.com"
    assert body["invite_url"] in mail.text
    assert "Acme &lt;Labs&gt;" in (mail.html or "")  # escaped in HTML
    assert "email.send" in env["runner"].completed


def test_accept_flow_joins_and_switches_org(env) -> None:
    token = token_of(invite(env, email="new@x.com", role="viewer"))
    c = env["c"]
    c.cookies.clear()
    preview = c.get(f"/api/v1/invitations/{token}").json()
    assert preview == {
        "organization": "Acme <Labs>",
        "role": "viewer",
        "email_hint": "ne*@x.com",
        "invited_by": "owner",
        "status": "pending",
    }
    uid = make_user(env["db"], "new@x.com")
    sign_in_client(c, env["db"], uid)
    assert c.post(f"/api/v1/invitations/{token}/accept").status_code == 204
    me = c.get("/api/v1/auth/me").json()
    assert me["active_organization_id"] == str(env["org"]) and me["role"] == "viewer"
    again = c.post(f"/api/v1/invitations/{token}/accept")
    assert again.status_code == 409 and again.json()["code"] == "invitation_accepted"


def test_link_does_not_work_for_another_account(env) -> None:
    token = token_of(invite(env, email="new@x.com"))
    intruder = make_user(env["db"], "intruder@evil.com")
    c = sign_in_client(env["c"], env["db"], intruder)
    r = c.post(f"/api/v1/invitations/{token}/accept")
    assert r.status_code == 403 and r.json()["code"] == "wrong_account"


def test_expired_and_revoked(env) -> None:
    r = invite(env, email="late@x.com")
    token, inv_id = token_of(r), r.json()["id"]
    with env["db"].session() as s:
        set_tenant(s, bypass=True)
        inv = s.get(Invitation, __import__("uuid").UUID(inv_id))
        assert inv is not None
        inv.expires_at = utcnow() - timedelta(minutes=1)
    uid = make_user(env["db"], "late@x.com")
    c = sign_in_client(env["c"], env["db"], uid)
    assert c.post(f"/api/v1/invitations/{token}/accept").json()["code"] == "invitation_expired"

    r = invite(env, email="gone@x.com")
    c = sign_in_client(env["c"], env["db"], env["owner"], env["org"])
    assert c.delete(f"/api/v1/org/invitations/{r.json()['id']}").status_code == 204
    gone = make_user(env["db"], "gone@x.com")
    c = sign_in_client(env["c"], env["db"], gone)
    assert (
        c.post(f"/api/v1/invitations/{token_of(r)}/accept").json()["code"] == "invitation_revoked"
    )


def test_reinvite_replaces_pending(env) -> None:
    first = token_of(invite(env, email="twice@x.com"))
    second = token_of(invite(env, email="twice@x.com"))
    c = sign_in_client(env["c"], env["db"], env["owner"], env["org"])
    assert [i["email"] for i in c.get("/api/v1/org/invitations").json()] == ["twice@x.com"]
    uid = make_user(env["db"], "twice@x.com")
    c = sign_in_client(env["c"], env["db"], uid)
    assert c.post(f"/api/v1/invitations/{first}/accept").status_code == 409
    assert c.post(f"/api/v1/invitations/{second}/accept").status_code == 204


def test_permissions(env) -> None:
    assert invite(env, actor="member").status_code == 403
    assert invite(env, actor="admin", role="owner").status_code == 403
    assert invite(env, actor="admin", role="admin").status_code == 201
    assert invite(env, actor="owner", role="owner", email="co@x.com").status_code == 201


def test_cannot_invite_existing_member(env) -> None:
    r = invite(env, email="member@acme.com")
    assert r.status_code == 409 and r.json()["code"] == "already_member"


def test_invalid_token(env) -> None:
    assert env["c"].get("/api/v1/invitations/nope").status_code == 404


def test_other_org_cannot_list_or_revoke(env) -> None:
    r = invite(env, email="x@x.com")
    other_org, other_owner = make_org(env["db"], "Other", "boss@other.com")
    c = sign_in_client(env["c"], env["db"], other_owner, other_org)
    assert c.get("/api/v1/org/invitations").json() == []
    assert c.delete(f"/api/v1/org/invitations/{r.json()['id']}").status_code == 404


def test_smtp_message_building() -> None:
    sender = SmtpSender(
        host="smtp.example.com",
        port=587,
        username=None,
        password=None,
        security="starttls",
        from_address="CloudCostRadar <no-reply@ccradar.example>",
    )
    mime = sender.build(Email(to="a@b.com", subject="Hi", text="plain", html="<p>rich</p>"))
    parsed = message_from_bytes(mime.as_bytes())
    assert parsed["To"] == "a@b.com" and parsed["From"].startswith("CloudCostRadar")
    assert parsed.is_multipart()
    assert {p.get_content_type() for p in parsed.walk()} >= {"text/plain", "text/html"}
