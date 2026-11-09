"""The signed-in person's notification inbox and their notification choices (9.4)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel
from sqlalchemy import func, select, update

from costlens.api.deps import CurrentAuth, SessionDep
from costlens.core import inbox
from costlens.core.errors import AppError, NotFoundError
from costlens.db.base import utcnow
from costlens.models import InboxItem, Membership, Organization

router = APIRouter(prefix="/api/v1/me", tags=["notifications"])


class ItemOut(BaseModel):
    id: uuid.UUID
    event: str
    title: str
    text: str
    link: str | None
    severity: str
    organization_id: uuid.UUID
    organization: str
    created_at: datetime
    read_at: datetime | None


class InboxOut(BaseModel):
    items: list[ItemOut]
    unread: int
    next_before: datetime | None


class Unread(BaseModel):
    unread: int


class Pref(BaseModel):
    event: str
    label: str = ""
    inbox: bool
    email: bool


def _unread(db: SessionDep, user_id: uuid.UUID) -> int:
    q = select(func.count()).where(InboxItem.user_id == user_id, InboxItem.read_at.is_(None))
    return int(db.scalar(q) or 0)


@router.get("/notifications", response_model=InboxOut)
def list_notifications(
    auth: CurrentAuth,
    db: SessionDep,
    unread_only: bool = False,
    before: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> InboxOut:
    """Newest first, from every organisation the person belongs to."""
    with inbox.as_owner_of_items(db):
        q = (
            select(InboxItem, Organization.name)
            .join(Organization, Organization.id == InboxItem.org_id)
            .where(InboxItem.user_id == auth.user.id)
        )
        if unread_only:
            q = q.where(InboxItem.read_at.is_(None))
        if before is not None:
            q = q.where(InboxItem.created_at < before)
        rows = db.execute(q.order_by(InboxItem.created_at.desc()).limit(limit + 1)).all()
        unread = _unread(db, auth.user.id)
    items = [
        ItemOut(
            id=i.id,
            event=i.event,
            title=i.title,
            text=i.text,
            link=i.link,
            severity=i.severity,
            organization_id=i.org_id,
            organization=name,
            created_at=i.created_at,
            read_at=i.read_at,
        )
        for i, name in rows[:limit]
    ]
    more = len(rows) > limit
    return InboxOut(items=items, unread=unread, next_before=items[-1].created_at if more else None)


@router.get("/notifications/unread", response_model=Unread)
def unread_count(auth: CurrentAuth, db: SessionDep) -> Unread:
    with inbox.as_owner_of_items(db):
        return Unread(unread=_unread(db, auth.user.id))


@router.post("/notifications/{item_id}/read", status_code=204)
def mark_read(item_id: uuid.UUID, auth: CurrentAuth, db: SessionDep) -> Response:
    with inbox.as_owner_of_items(db):
        item = db.get(InboxItem, item_id)
        if item is None or item.user_id != auth.user.id:
            raise NotFoundError("Notification not found.")
        if item.read_at is None:
            item.read_at = utcnow()
            db.flush()
    return Response(status_code=204)


@router.post("/notifications/read-all", response_model=Unread)
def mark_all_read(auth: CurrentAuth, db: SessionDep) -> Unread:
    with inbox.as_owner_of_items(db):
        db.execute(
            update(InboxItem)
            .where(InboxItem.user_id == auth.user.id, InboxItem.read_at.is_(None))
            .values(read_at=utcnow())
        )
        db.flush()
    return Unread(unread=0)


def _membership(db: SessionDep, auth: CurrentAuth) -> Membership:
    if auth.org_id is None:
        raise AppError("Choose an organisation first.", status=409, code="no_organization")
    m = db.scalar(
        select(Membership).where(
            Membership.org_id == auth.org_id, Membership.user_id == auth.user.id
        )
    )
    if m is None:
        raise NotFoundError("You are not a member of this organisation.")
    return m


def _prefs(m: Membership) -> list[Pref]:
    chosen = m.notification_prefs or {}
    off, email = set(chosen.get("inbox_off", [])), set(chosen.get("email", []))
    admin = m.role in inbox.ADMIN_ROLES
    return [
        Pref(event=e, label=label, inbox=e not in off, email=e in email)
        for e, (label, audience) in inbox.EVENTS.items()
        if audience != "admins" or admin
    ]


@router.get("/notification-preferences", response_model=list[Pref])
def get_preferences(auth: CurrentAuth, db: SessionDep) -> list[Pref]:
    """What this person receives in the active organisation, per kind of notification."""
    return _prefs(_membership(db, auth))


@router.put("/notification-preferences", response_model=list[Pref])
def set_preferences(body: list[Pref], auth: CurrentAuth, db: SessionDep) -> list[Pref]:
    m = _membership(db, auth)
    unknown = sorted({p.event for p in body} - set(inbox.EVENTS))
    if unknown:
        raise AppError(
            f"Unknown notification kinds: {', '.join(unknown)}.", status=422, code="bad_event"
        )
    current = m.notification_prefs or {}
    off, email = set(current.get("inbox_off", [])), set(current.get("email", []))
    for p in body:
        (off.discard if p.inbox else off.add)(p.event)
        (email.add if p.email else email.discard)(p.event)
    m.notification_prefs = {"inbox_off": sorted(off), "email": sorted(email)}
    db.flush()
    return _prefs(m)
