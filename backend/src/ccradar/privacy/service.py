"""Privacy rights: export a person's or an organisation's data; delete accounts and
organisations (DPDP Act 2023, GDPR).

* Personal export: everything stored about the person, as JSON, immediately.
* Organisation export: one JSON file per tenant table in a ZIP, built by a job;
  encrypted secrets and token hashes are left out. Downloadable for 7 days.
* Account deletion: refused while the person is the last owner of an
  organisation; their audit entries are pseudonymised (actor label, IP, user agent).
* Organisation deletion: hidden immediately, restorable for the grace period, then
  purged: deleting the organisation row cascades to every tenant table.
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from ccradar import __version__
from ccradar.accounts.roles import Role
from ccradar.audit.recorder import record
from ccradar.core.crypto import EncryptedString
from ccradar.core.errors import AppError, ConflictError, NotFoundError
from ccradar.core.storage import Storage
from ccradar.db.base import Base, utcnow
from ccradar.db.tenant import set_tenant, tenant_tables
from ccradar.models import (
    AuditEvent,
    DataExport,
    Identity,
    Membership,
    Organization,
    User,
    UserSession,
)

EXPORT_TTL = timedelta(days=7)
DELETION_GRACE = timedelta(days=30)
# Never exported: they are credentials or derived from credentials.
EXCLUDED_COLUMNS = frozenset({"token_hash"})
PSEUDONYM = "deleted user"


def _plain(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (uuid.UUID, Decimal)):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    return value


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


# --- personal data --------------------------------------------------------------


def export_user(db: Session, user: User) -> dict[str, Any]:
    set_tenant(db, bypass=True)
    try:
        memberships = db.execute(
            select(Organization.name, Organization.slug, Membership.role, Membership.created_at)
            .join(Organization, Organization.id == Membership.org_id)
            .where(Membership.user_id == user.id)
        ).all()
        actions = db.scalars(
            select(AuditEvent)
            .where(AuditEvent.actor_user_id == user.id)
            .order_by(AuditEvent.occurred_at)
        ).all()
        sessions = db.scalars(select(UserSession).where(UserSession.user_id == user.id)).all()
        identities = db.scalars(select(Identity).where(Identity.user_id == user.id)).all()
    finally:
        set_tenant(db, user_id=user.id)
    return {
        "exported_at": utcnow().isoformat(),
        "product": f"CloudCostRadar {__version__}",
        "profile": {
            "id": str(user.id),
            "email": user.email,
            "name": user.name,
            "avatar_url": user.avatar_url,
            "created_at": _plain(user.created_at),
            "last_login_at": _plain(user.last_login_at),
        },
        "sign_in_methods": [
            {"provider": i.provider, "email": i.email, "linked_at": _plain(i.created_at)}
            for i in identities
        ],
        "organizations": [
            {"name": n, "slug": s, "role": _plain(r), "joined_at": _plain(c)}
            for n, s, r, c in memberships
        ],
        "sessions": [
            {
                "created_at": _plain(s.created_at),
                "last_seen_at": _plain(s.last_seen_at),
                "ip": s.ip,
                "user_agent": s.user_agent,
                "revoked_at": _plain(s.revoked_at),
            }
            for s in sessions
        ],
        "actions": [
            {"at": _plain(a.occurred_at), "action": a.action, "summary": a.summary, "ip": a.ip}
            for a in actions
        ],
    }


def sole_owner_of(db: Session, user_id: uuid.UUID) -> list[str]:
    """Names of organisations where this user is the only owner."""
    set_tenant(db, bypass=True)
    owned = db.execute(
        select(Membership.org_id, Organization.name)
        .join(Organization, Organization.id == Membership.org_id)
        .where(
            Membership.user_id == user_id,
            Membership.role == Role.OWNER,
            Organization.deletion_requested_at.is_(None),
        )
    ).all()
    names = []
    for org_id, name in owned:
        owners = db.scalar(
            select(func.count())
            .select_from(Membership)
            .where(Membership.org_id == org_id, Membership.role == Role.OWNER)
        )
        if owners == 1:
            names.append(name)
    return names


def delete_user(db: Session, user: User) -> None:
    blocking = sole_owner_of(db, user.id)
    if blocking:
        raise ConflictError(
            "You are the only owner of: "
            + ", ".join(blocking)
            + ". Transfer ownership or delete those organisations first.",
            code="sole_owner",
        )
    set_tenant(db, bypass=True)
    org_ids = list(db.scalars(select(Membership.org_id).where(Membership.user_id == user.id)))
    for org_id in org_ids:
        record(
            db,
            org_id=org_id,
            action="member.account_deleted",
            target_type="user",
            target_id=user.id,
            summary="a member deleted their account",
        )
    user_id = user.id
    db.delete(user)  # identities, memberships and sessions cascade (and are audited)
    db.flush()
    # Then pseudonymise their whole trail, including the entries just written.
    db.execute(
        update(AuditEvent)
        .where(AuditEvent.actor_user_id == user_id)
        .values(actor_label=PSEUDONYM, ip=None, user_agent=None)
        .execution_options(synchronize_session=False)
    )


# --- organisation export --------------------------------------------------------


def request_org_export(db: Session, org_id: uuid.UUID, user_id: uuid.UUID) -> DataExport:
    export = DataExport(org_id=org_id, requested_by_id=user_id, status="pending")
    db.add(export)
    db.flush()
    return export


def build_org_export(db: Session, storage: Storage, export_id: uuid.UUID) -> DataExport:
    set_tenant(db, bypass=True)
    export = db.get(DataExport, export_id)
    if export is None:
        raise NotFoundError("Export not found.")
    org_id = export.org_id
    buffer = io.BytesIO()
    manifest: dict[str, Any] = {
        "organization_id": str(org_id),
        "created_at": utcnow().isoformat(),
        "product": f"CloudCostRadar {__version__}",
        "tables": {},
    }
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        org = db.get(Organization, org_id)
        archive.writestr(
            "organization.json",
            json.dumps(
                {c.name: _plain(getattr(org, c.key)) for c in Organization.__table__.columns},
                indent=2,
            ),
        )
        for table in Base.metadata.sorted_tables:
            if table.name not in tenant_tables():
                continue
            columns = [
                c
                for c in table.columns
                if c.name not in EXCLUDED_COLUMNS and not isinstance(c.type, EncryptedString)
            ]
            rows = db.execute(select(*columns).where(table.c.org_id == org_id)).all()
            data = [{c.name: _plain(v) for c, v in zip(columns, row, strict=True)} for row in rows]
            archive.writestr(f"tables/{table.name}.json", json.dumps(data, indent=2))
            manifest["tables"][table.name] = len(data)
        archive.writestr("manifest.json", json.dumps(manifest, indent=2))
    key = f"exports/{org_id}/{export.id}.zip"
    storage.put(key, buffer.getvalue())
    export.status = "ready"
    export.storage_key = key
    export.size_bytes = len(buffer.getvalue())
    export.expires_at = utcnow() + EXPORT_TTL
    db.flush()
    return export


def export_available(export: DataExport) -> bool:
    return (
        export.status == "ready"
        and export.storage_key is not None
        and export.expires_at is not None
        and _aware(export.expires_at) > utcnow()
    )


# --- organisation deletion --------------------------------------------------------


def request_org_deletion(db: Session, org: Organization) -> datetime:
    if org.is_demo:
        raise AppError("The demo organisation cannot be deleted.", status=403, code="demo")
    requested = utcnow()
    org.deletion_requested_at = requested
    record(
        db,
        org_id=org.id,
        action="organization.deletion_requested",
        target_type="organization",
        target_id=org.id,
        summary="deletion requested; data will be purged after the grace period",
    )
    db.flush()
    return requested + DELETION_GRACE


def restore_org(db: Session, org: Organization) -> None:
    if org.deletion_requested_at is None:
        raise ConflictError("This organisation is not scheduled for deletion.", code="not_pending")
    org.deletion_requested_at = None
    record(
        db,
        org_id=org.id,
        action="organization.restored",
        target_type="organization",
        target_id=org.id,
        summary="deletion cancelled",
    )
    db.flush()


def purge_deleted_orgs(
    db: Session, storage: Storage, *, now: datetime | None = None, grace: timedelta = DELETION_GRACE
) -> list[uuid.UUID]:
    """Permanently delete organisations whose grace period is over."""
    cutoff = (now or utcnow()) - grace
    set_tenant(db, bypass=True)
    due = db.scalars(
        select(Organization).where(
            Organization.deletion_requested_at.is_not(None),
            Organization.deletion_requested_at <= cutoff,
        )
    ).all()
    purged = []
    for org in due:
        for key in db.scalars(
            select(DataExport.storage_key).where(
                DataExport.org_id == org.id, DataExport.storage_key.is_not(None)
            )
        ):
            if key:
                storage.delete(key)
        # Table-level delete (no ORM loading): foreign keys cascade to every tenant table.
        table = Base.metadata.tables["organizations"]
        db.execute(delete(table).where(table.c.id == org.id))
        purged.append(org.id)
    db.flush()
    return purged


def cleanup_expired_exports(db: Session, storage: Storage) -> int:
    set_tenant(db, bypass=True)
    expired = db.scalars(
        select(DataExport).where(
            DataExport.expires_at.is_not(None), DataExport.expires_at <= utcnow()
        )
    ).all()
    for export in expired:
        if export.storage_key:
            storage.delete(export.storage_key)
        db.delete(export)
    db.flush()
    return len(expired)
