from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ccradar.accounts import service as acc
from ccradar.accounts.roles import Permission, Role, can_assign, has_permission
from ccradar.core.errors import AppError, ConflictError, NotFoundError, PermissionDeniedError
from ccradar.db import Database
from ccradar.db.base import utcnow
from ccradar.db.tenant import tenant_tables
from ccradar.models import Membership, Organization, User, UserSession


@pytest.fixture
def s(db: Database) -> Iterator[Session]:
    with db.session() as session:
        yield session


def user(s: Session, email: str, *, verified: bool = True) -> User:
    return acc.sign_in_with_identity(
        s, provider="google", subject=f"sub-{email.lower()}", email=email, email_verified=verified
    )


# --- roles ------------------------------------------------------------------


def test_role_order_and_permissions() -> None:
    assert Role.OWNER.at_least(Role.ADMIN) and not Role.MEMBER.at_least(Role.ADMIN)
    assert has_permission(Role.VIEWER, Permission.READ)
    assert not has_permission(Role.VIEWER, Permission.WRITE)
    assert has_permission(Role.ADMIN, Permission.MANAGE_CONNECTIONS)
    assert not has_permission(Role.ADMIN, Permission.MANAGE_BILLING)
    assert has_permission(Role.OWNER, Permission.DELETE_ORGANISATION)


@pytest.mark.parametrize(
    ("actor", "current", "new", "allowed"),
    [
        (Role.ADMIN, Role.VIEWER, Role.ADMIN, True),
        (Role.ADMIN, Role.MEMBER, Role.OWNER, False),
        (Role.ADMIN, Role.OWNER, Role.MEMBER, False),
        (Role.OWNER, Role.OWNER, Role.ADMIN, True),
        (Role.MEMBER, Role.VIEWER, Role.MEMBER, False),
    ],
)
def test_can_assign(actor: Role, current: Role, new: Role, allowed: bool) -> None:
    assert can_assign(actor, current, new) is allowed


# --- users ------------------------------------------------------------------


def test_sign_in_creates_then_reuses_user(s: Session) -> None:
    u1 = user(s, "Asha@Example.com")
    assert u1.email == "asha@example.com"
    assert u1.name == "asha"
    assert u1.last_login_at is not None
    u2 = user(s, "asha@example.com")
    assert u1.id == u2.id
    assert len(u1.identities) == 1


def test_second_provider_links_by_verified_email(s: Session) -> None:
    u1 = user(s, "ravi@example.com")
    u2 = acc.sign_in_with_identity(
        s, provider="oidc:okta", subject="okta-1", email="ravi@example.com", email_verified=True
    )
    assert u1.id == u2.id
    assert {i.provider for i in u2.identities} == {"google", "oidc:okta"}


def test_unverified_email_cannot_take_over_account(s: Session) -> None:
    user(s, "victim@example.com")
    with pytest.raises(PermissionDeniedError) as exc:
        acc.sign_in_with_identity(
            s, provider="oidc:x", subject="evil", email="victim@example.com", email_verified=False
        )
    assert exc.value.code == "email_not_verified"


def test_disabled_user_cannot_sign_in(s: Session) -> None:
    u = user(s, "gone@example.com")
    u.is_active = False
    with pytest.raises(PermissionDeniedError):
        user(s, "gone@example.com")


def test_invalid_email_rejected(s: Session) -> None:
    with pytest.raises(AppError):
        user(s, "not-an-email")


def test_email_unique(s: Session) -> None:
    s.add(User(email="dup@example.com"))
    s.add(User(email="dup@example.com"))
    with pytest.raises(IntegrityError):
        s.flush()
    s.rollback()


# --- organisations ------------------------------------------------------------


def test_create_organization_makes_owner_and_unique_slug(s: Session) -> None:
    owner = user(s, "o@example.com")
    a = acc.create_organization(s, name="Acme Corp!", owner=owner)
    b = acc.create_organization(s, name="Acme Corp", owner=owner)
    assert a.slug == "acme-corp"
    assert b.slug.startswith("acme-corp-") and b.slug != a.slug
    orgs = acc.user_organizations(s, owner.id)
    assert [role for _, role in orgs] == [Role.OWNER, Role.OWNER]


def test_slugify_edge_cases() -> None:
    assert acc.slugify("  ***  ") == "org"
    assert acc.slugify("தமிழ் Cloud") == "cloud"
    assert len(acc.slugify("x" * 300)) <= 50


def test_organization_name_validated(s: Session) -> None:
    with pytest.raises(AppError):
        acc.create_organization(s, name="   ", owner=user(s, "o@example.com"))


def test_pending_deletion_hidden_from_user_list(s: Session) -> None:
    owner = user(s, "o@example.com")
    org = acc.create_organization(s, name="Old", owner=owner)
    org.deletion_requested_at = utcnow()
    assert acc.user_organizations(s, owner.id) == []


# --- memberships ------------------------------------------------------------


@pytest.fixture
def team(s: Session) -> dict[str, object]:
    owner = user(s, "owner@example.com")
    admin = user(s, "admin@example.com")
    member = user(s, "member@example.com")
    outsider = user(s, "out@example.com")
    org = acc.create_organization(s, name="Team", owner=owner)
    acc.add_member(s, org_id=org.id, actor_id=owner.id, user=admin, role=Role.ADMIN)
    acc.add_member(s, org_id=org.id, actor_id=admin.id, user=member, role=Role.MEMBER)
    return {"org": org, "owner": owner, "admin": admin, "member": member, "outsider": outsider}


def test_admin_cannot_add_owner(s: Session, team: dict) -> None:
    with pytest.raises(PermissionDeniedError):
        acc.add_member(
            s,
            org_id=team["org"].id,
            actor_id=team["admin"].id,
            user=team["outsider"],
            role=Role.OWNER,
        )


def test_member_cannot_manage_members(s: Session, team: dict) -> None:
    with pytest.raises(PermissionDeniedError):
        acc.add_member(
            s,
            org_id=team["org"].id,
            actor_id=team["member"].id,
            user=team["outsider"],
            role=Role.VIEWER,
        )


def test_outsider_sees_not_found(s: Session, team: dict) -> None:
    with pytest.raises(NotFoundError):
        acc.require_permission(s, team["org"].id, team["outsider"].id, Permission.READ)


def test_duplicate_member(s: Session, team: dict) -> None:
    with pytest.raises(ConflictError):
        acc.add_member(
            s,
            org_id=team["org"].id,
            actor_id=team["owner"].id,
            user=team["member"],
            role=Role.VIEWER,
        )


def test_last_owner_protected(s: Session, team: dict) -> None:
    org, owner = team["org"], team["owner"]
    with pytest.raises(ConflictError) as exc:
        acc.change_role(s, org_id=org.id, actor_id=owner.id, user_id=owner.id, role=Role.ADMIN)
    assert exc.value.code == "last_owner"
    with pytest.raises(ConflictError):
        acc.remove_member(s, org_id=org.id, actor_id=owner.id, user_id=owner.id)


def test_ownership_transfer(s: Session, team: dict) -> None:
    org, owner, admin = team["org"], team["owner"], team["admin"]
    acc.change_role(s, org_id=org.id, actor_id=owner.id, user_id=admin.id, role=Role.OWNER)
    acc.change_role(s, org_id=org.id, actor_id=admin.id, user_id=owner.id, role=Role.MEMBER)
    assert acc.get_membership(s, org.id, owner.id).role is Role.MEMBER  # type: ignore[union-attr]


def test_admin_cannot_demote_or_remove_owner(s: Session, team: dict) -> None:
    org, owner, admin = team["org"], team["owner"], team["admin"]
    with pytest.raises(PermissionDeniedError):
        acc.change_role(s, org_id=org.id, actor_id=admin.id, user_id=owner.id, role=Role.VIEWER)
    with pytest.raises(PermissionDeniedError):
        acc.remove_member(s, org_id=org.id, actor_id=admin.id, user_id=owner.id)


def test_member_can_leave_and_admin_can_remove(s: Session, team: dict) -> None:
    org = team["org"]
    acc.remove_member(s, org_id=org.id, actor_id=team["member"].id, user_id=team["member"].id)
    acc.remove_member(s, org_id=org.id, actor_id=team["owner"].id, user_id=team["admin"].id)
    assert acc.get_membership(s, org.id, team["member"].id) is None
    assert acc.get_membership(s, org.id, team["admin"].id) is None


def test_deleting_organization_cascades_memberships(s: Session, team: dict) -> None:
    s.delete(team["org"])
    s.flush()
    assert s.scalars(select(Membership)).all() == []


def test_tenant_registry() -> None:
    assert "memberships" in tenant_tables()
    assert "users" not in tenant_tables()
    assert "organizations" not in tenant_tables()


# --- sessions -----------------------------------------------------------------


def test_session_lifecycle(s: Session, team: dict) -> None:
    owner = team["owner"]
    token, record = acc.create_session(s, owner, ip="203.0.113.5", user_agent="pytest")
    assert record.token_hash != token and len(record.token_hash) == 64
    assert record.active_org_id == team["org"].id
    assert acc.resolve_session(s, token) is record
    assert acc.resolve_session(s, token + "x") is None
    assert acc.resolve_session(s, "") is None
    acc.revoke_session(s, record)
    assert acc.resolve_session(s, token) is None


def test_session_expiry_and_touch(s: Session, team: dict) -> None:
    now = utcnow()
    token, record = acc.create_session(s, team["owner"], ttl=timedelta(hours=1), now=now)
    later = now + timedelta(minutes=10)
    assert acc.resolve_session(s, token, now=later) is record
    assert record.last_seen_at == later
    assert acc.resolve_session(s, token, now=now + timedelta(hours=2)) is None


def test_session_rejected_for_disabled_user(s: Session, team: dict) -> None:
    token, _ = acc.create_session(s, team["member"])
    team["member"].is_active = False
    assert acc.resolve_session(s, token) is None


def test_switch_organization(s: Session, team: dict) -> None:
    owner = team["owner"]
    other = acc.create_organization(s, name="Second", owner=owner)
    _, record = acc.create_session(s, owner)
    acc.switch_organization(s, record, other.id)
    assert record.active_org_id == other.id
    foreign = acc.create_organization(s, name="Foreign", owner=team["outsider"])
    with pytest.raises(NotFoundError):
        acc.switch_organization(s, record, foreign.id)


def test_revoke_all_sessions(s: Session, team: dict) -> None:
    acc.create_session(s, team["owner"])
    acc.create_session(s, team["owner"])
    assert acc.revoke_all_sessions(s, team["owner"].id) == 2
    live = s.scalars(select(UserSession).where(UserSession.revoked_at.is_(None))).all()
    assert live == []


def test_session_survives_reload_from_database(db: Database) -> None:
    """Datetimes read back from SQLite are naive; resolution must still work."""
    with db.session() as s:
        token, _ = acc.create_session(s, user(s, "reload@example.com"))
    with db.session() as s:
        assert acc.resolve_session(s, token) is not None
        assert s.scalar(select(Organization)) is None
