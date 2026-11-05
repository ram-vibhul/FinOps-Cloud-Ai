"""Test helpers: users, organisations and signed-in clients without a real IdP."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi.testclient import TestClient

from costlens.accounts import service as acc
from costlens.accounts.roles import Role
from costlens.core.csrf import csrf_token
from costlens.db import Database
from costlens.db.tenant import set_tenant
from costlens.models import Membership, User


@dataclass
class Actor:
    user_id: uuid.UUID
    org_id: uuid.UUID
    token: str


def make_user(db: Database, email: str) -> uuid.UUID:
    with db.session() as s:
        return acc.sign_in_with_identity(
            s, provider="test", subject=email, email=email, email_verified=True
        ).id


def make_org(db: Database, name: str, owner_email: str) -> tuple[uuid.UUID, uuid.UUID]:
    """Returns (org_id, owner_user_id)."""
    owner_id = make_user(db, owner_email)
    with db.session() as s:
        owner = s.get(User, owner_id)
        assert owner is not None
        return acc.create_organization(s, name=name, owner=owner).id, owner_id


def add_to_org(db: Database, org_id: uuid.UUID, email: str, role: Role) -> uuid.UUID:
    user_id = make_user(db, email)
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        s.add(Membership(org_id=org_id, user_id=user_id, role=role))
    return user_id


def session_for(db: Database, user_id: uuid.UUID, org_id: uuid.UUID | None = None) -> str:
    with db.session() as s:
        user = s.get(User, user_id)
        assert user is not None
        token, record = acc.create_session(s, user)
        if org_id is not None:
            acc.switch_organization(s, record, org_id)
        return token


def sign_in_client(
    client: TestClient, db: Database, user_id: uuid.UUID, org_id: uuid.UUID | None = None
) -> TestClient:
    token = session_for(db, user_id, org_id)
    client.cookies.clear()
    client.cookies.set("costlens_session", token)
    client.headers["X-CSRF-Token"] = csrf_token(client.app.state.signing_key, token)  # type: ignore[attr-defined]
    return client
