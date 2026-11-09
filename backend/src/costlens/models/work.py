"""Work-tracking integrations (Jira, ServiceNow, GitHub, GitLab) and the tickets and pull
requests CostLens opened from findings."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from costlens.core.crypto import EncryptedString
from costlens.db.base import Base, Timestamps, UUIDPrimaryKey
from costlens.db.tenant import TenantMixin


class WorkIntegration(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "work_integrations"
    __audited__ = True
    __audit_redact__ = frozenset({"token"})
    __audit_ignore__ = frozenset({"last_used_at", "last_error"})

    kind: Mapped[str] = mapped_column(String(20))  # jira | servicenow | github | gitlab
    name: Mapped[str] = mapped_column(String(100))
    base_url: Mapped[str] = mapped_column(String(500))
    username: Mapped[str | None] = mapped_column(String(200))  # Jira email / ServiceNow user
    token: Mapped[str] = mapped_column(EncryptedString(2000))
    # jira: project_key, issue_type; servicenow: table; github: repo, base_branch;
    # gitlab: project, base_branch
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))


class FindingLink(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "finding_links"
    __audited__ = True

    finding_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("findings.id", ondelete="CASCADE"), index=True
    )
    integration_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("work_integrations.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String(20))  # ticket | pull_request
    provider: Mapped[str] = mapped_column(String(20))
    external_id: Mapped[str] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(String(1000))
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
