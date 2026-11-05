"""The demo organisation: realistic sample data anyone can explore read-only.

Each feature registers a *seeder* that fills its own tables for the demo
organisation. Seeders get a deterministic random generator and "today", so the
demo looks the same for everyone and always ends on the current date.
Reset deletes the demo organisation's data and runs every seeder again.
"""

from __future__ import annotations

import logging
import random
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from costlens.accounts.roles import Role
from costlens.core.errors import AppError
from costlens.db.base import Base, utcnow
from costlens.db.tenant import set_tenant, tenant_tables
from costlens.models import Membership, Organization

log = logging.getLogger(__name__)
DEMO_SLUG = "demo"
DEMO_NAME = "Demo Company"
DEMO_SEED = 20261105


@dataclass(frozen=True)
class DemoContext:
    org_id: uuid.UUID
    today: date
    rng: random.Random


Seeder = Callable[[Session, DemoContext], None]
_SEEDERS: list[tuple[int, str, Seeder]] = []


def demo_seeder(name: str, order: int = 100) -> Callable[[Seeder], Seeder]:
    """Register a seeder; lower ``order`` runs first (e.g. accounts before costs)."""

    def decorator(fn: Seeder) -> Seeder:
        if any(n == name for _, n, _ in _SEEDERS):
            raise ValueError(f"demo seeder {name!r} already registered")
        _SEEDERS.append((order, name, fn))
        _SEEDERS.sort(key=lambda item: (item[0], item[1]))
        return fn

    return decorator


def seeders() -> list[str]:
    return [name for _, name, _ in _SEEDERS]


# Feature modules that register demo seeders. Loaded by name (not plain imports) so that
# automatic "unused import" clean-ups can never silently drop them.
SEEDER_MODULES = (
    "costlens.costs.demo",
    "costlens.inventory.demo",
    "costlens.changes.demo",
    "costlens.costs.anomaly_demo",
)


def load_all_seeders() -> None:
    """Import feature modules that register demo seeders."""
    import importlib

    for module in SEEDER_MODULES:
        importlib.import_module(module)


def get_demo_org(db: Session) -> Organization | None:
    return db.scalar(
        select(Organization).where(Organization.slug == DEMO_SLUG, Organization.is_demo.is_(True))
    )


def ensure_demo(db: Session, *, today: date | None = None) -> Organization:
    """Create the demo organisation if missing and seed it."""
    set_tenant(db, bypass=True)
    org = get_demo_org(db)
    if org is None:
        if db.scalar(select(Organization).where(Organization.slug == DEMO_SLUG)) is not None:
            raise RuntimeError("an organisation already uses the slug 'demo'")
        org = Organization(name=DEMO_NAME, slug=DEMO_SLUG, is_demo=True, default_currency="USD")
        db.add(org)
        db.flush()
        _run_seeders(db, org.id, today)
    return org


def reset_demo(db: Session, *, today: date | None = None) -> Organization:
    """Delete all demo data except who has joined, then seed again."""
    set_tenant(db, bypass=True)
    org = get_demo_org(db)
    if org is None:
        return ensure_demo(db, today=today)
    keep = {"memberships"}
    for table in reversed(Base.metadata.sorted_tables):
        if table.name in tenant_tables() and table.name not in keep:
            db.execute(delete(table).where(table.c.org_id == org.id))
    _run_seeders(db, org.id, today)
    return org


def _run_seeders(db: Session, org_id: uuid.UUID, today: date | None) -> None:
    load_all_seeders()
    set_tenant(db, org_id=org_id, bypass=True)
    for _, name, fn in _SEEDERS:
        rng = random.Random(f"{DEMO_SEED}:{name}")  # noqa: S311 - sample data, not secrets
        ctx = DemoContext(org_id=org_id, today=today or utcnow().date(), rng=rng)
        fn(db, ctx)
        log.info("demo seeder %s done", name)
    db.flush()


def join_demo(db: Session, user_id: uuid.UUID) -> Organization:
    """Add the user to the demo organisation as a viewer (idempotent)."""
    org = ensure_demo(db)
    set_tenant(db, org_id=org.id, user_id=user_id)
    exists = db.scalar(
        select(Membership).where(Membership.org_id == org.id, Membership.user_id == user_id)
    )
    if exists is None:
        db.add(Membership(org_id=org.id, user_id=user_id, role=Role.VIEWER))
        db.flush()
    return org


class DemoReadOnlyError(AppError):
    status = 403
    code = "demo_read_only"
