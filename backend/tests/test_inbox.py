from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from costlens.accounts.roles import Role
from costlens.core.email import MemorySender, configure_email
from costlens.core.notify import Message, notify
from costlens.db import Database
from costlens.db.base import utcnow
from costlens.db.tenant import set_tenant
from costlens.models import CloudConnection, InboxItem
from tests.helpers import add_to_org, make_org, sign_in_client


def send(db: Database, org_id, event: str, title: str = "Hello") -> None:
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        notify(s, event, [Message(title=title, text="Details", link="/costs")])


def inbox_of(db: Database, user_id) -> list[tuple[str, str]]:
    with db.session() as s:
        set_tenant(s, bypass=True)
        rows = s.scalars(select(InboxItem).where(InboxItem.user_id == user_id)).all()
        return sorted((r.event, r.title) for r in rows)


def test_notify_fans_out_to_the_people_concerned(client: TestClient, db: Database) -> None:
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    admin = add_to_org(db, org_id, "a@acme.com", Role.ADMIN)
    viewer = add_to_org(db, org_id, "v@acme.com", Role.VIEWER)
    send(db, org_id, "budget_alert", "Over budget")
    send(db, org_id, "access_request", "Ravi asks for admin")  # approvals: admins only
    for who in (owner, admin):
        assert inbox_of(db, who) == [
            ("access_request", "Ravi asks for admin"),
            ("budget_alert", "Over budget"),
        ]
    assert inbox_of(db, viewer) == [("budget_alert", "Over budget")]
    # Another organisation's people never see it.
    _, stranger = make_org(db, "Other", "x@other.com")
    assert inbox_of(db, stranger) == []


def test_inbox_api_badge_and_mark_read(client: TestClient, db: Database) -> None:
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    other = add_to_org(db, org_id, "m@acme.com", Role.MEMBER)
    for n in range(3):
        send(db, org_id, "cost_anomaly", f"Spike {n}")
    c = sign_in_client(client, db, owner, org_id)
    assert c.get("/api/v1/me/notifications/unread").json() == {"unread": 3}
    page = c.get("/api/v1/me/notifications", params={"limit": 2}).json()
    assert page["unread"] == 3 and len(page["items"]) == 2 and page["next_before"]
    first = page["items"][0]
    assert first["organization"] == "Acme" and first["link"] == "/costs"
    assert c.post(f"/api/v1/me/notifications/{first['id']}/read").status_code == 204
    assert c.get("/api/v1/me/notifications/unread").json() == {"unread": 2}
    unread = c.get("/api/v1/me/notifications", params={"unread_only": True}).json()
    assert len(unread["items"]) == 2
    # Someone else's notification cannot be touched.
    o = sign_in_client(client, db, other, org_id)
    assert o.post(f"/api/v1/me/notifications/{first['id']}/read").status_code == 404
    assert o.get("/api/v1/me/notifications/unread").json() == {"unread": 3}
    c = sign_in_client(client, db, owner, org_id)
    assert c.post("/api/v1/me/notifications/read-all").json() == {"unread": 0}
    assert c.get("/api/v1/me/notifications/unread").json() == {"unread": 0}


def test_preferences_mute_the_inbox_and_add_email(client: TestClient, db: Database) -> None:
    outbox = MemorySender()
    configure_email(outbox)
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    viewer = add_to_org(db, org_id, "v@acme.com", Role.VIEWER)
    v = sign_in_client(client, db, viewer, org_id)
    prefs = v.get("/api/v1/me/notification-preferences").json()
    events = {p["event"] for p in prefs}
    assert "budget_alert" in events and "access_request" not in events  # admins only
    assert all(p["inbox"] and not p["email"] for p in prefs)
    out = v.put(
        "/api/v1/me/notification-preferences",
        json=[
            {"event": "budget_alert", "inbox": False, "email": True},
            {"event": "weekly_digest", "inbox": False, "email": False},
        ],
    ).json()
    chosen = {p["event"]: (p["inbox"], p["email"]) for p in out}
    assert chosen["budget_alert"] == (False, True) and chosen["weekly_digest"] == (False, False)
    bad = v.put(
        "/api/v1/me/notification-preferences",
        json=[{"event": "nope", "inbox": True, "email": False}],
    )
    assert bad.status_code == 422
    send(db, org_id, "budget_alert", "Over budget")
    send(db, org_id, "weekly_digest", "Your week")
    send(db, org_id, "cost_anomaly", "Spike")
    assert inbox_of(db, viewer) == [("cost_anomaly", "Spike")]
    mails = [m for m in outbox.outbox if m.to == "v@acme.com"]
    assert [m.subject for m in mails] == ["CostLens: Over budget"]
    assert "/costs" in mails[0].text and "Notifications" in mails[0].text


def test_invitation_reaches_an_existing_users_inbox(client: TestClient, db: Database) -> None:
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    their_org, newcomer = make_org(db, "Startup", "Dev@Startup.io")
    c = sign_in_client(client, db, owner, org_id)
    c.post("/api/v1/org/invitations", json={"email": "dev@startup.io", "role": "member"})
    c.post("/api/v1/org/invitations", json={"email": "nobody@new.io", "role": "member"})
    n = sign_in_client(client, db, newcomer, their_org)  # seen from their own organisation
    items = n.get("/api/v1/me/notifications").json()["items"]
    assert [(i["event"], i["organization"], i["link"]) for i in items] == [
        ("invitation", "Acme", None)  # the secret link stays in the email
    ]
    assert "invited you to Acme" in items[0]["title"]


def test_import_failures_and_first_import_are_notified(client: TestClient, db: Database) -> None:
    from costlens.jobs.costs import _connection_state, _report_connection

    org_id, owner = make_org(db, "Acme", "o@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        conn = CloudConnection(
            org_id=org_id, provider="aws", name="Prod", status="active", external_id="x-1"
        )
        s.add(conn)
        s.flush()
        cid = str(conn.id)

    def run(error: str | None) -> None:
        before = _connection_state(cid)
        with db.session() as s:
            set_tenant(s, bypass=True)
            c = s.get(CloudConnection, conn.id)
            assert c is not None
            c.last_error, c.last_checked_at = error, utcnow()
        _report_connection(cid, before)

    run(None)  # first import
    run(None)  # routine: quiet
    run("AccessDenied")  # starts failing
    run("AccessDenied")  # still failing: quiet
    run(None)  # recovered
    assert [t for _, t in inbox_of(db, owner)] == [
        "Cost import failed: Prod",
        "Cost import working again: Prod",
        "First costs imported from Prod",
    ]


def test_old_notifications_follow_the_retention_setting(db: Database) -> None:
    from costlens.privacy.retention import apply_retention

    org_id, owner = make_org(db, "Acme", "o@acme.com")
    send(db, org_id, "budget_alert", "Old")
    send(db, org_id, "budget_alert", "New")
    with db.session() as s:
        set_tenant(s, bypass=True)
        old = s.scalars(select(InboxItem).where(InboxItem.title == "Old")).one()
        old.created_at = utcnow() - timedelta(days=91)  # default: 90 days
    with db.session() as s:
        assert apply_retention(s)["notifications"] == 1
    assert inbox_of(db, owner) == [("budget_alert", "New")]
