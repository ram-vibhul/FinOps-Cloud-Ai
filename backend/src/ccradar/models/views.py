"""Saved views (a named, reusable explorer query)."""

from __future__ import annotations

import uuid

from sqlalchemy import JSON, Boolean, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from ccradar.db.base import Base, Timestamps, UUIDPrimaryKey
from ccradar.db.tenant import TenantMixin


class SavedView(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "saved_views"
    __audited__ = True

    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(30), default="cost_explorer")
    owner_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    shared: Mapped[bool] = mapped_column(Boolean, default=False)  # visible to the whole org
    query: Mapped[dict] = mapped_column(JSON, default=dict)
