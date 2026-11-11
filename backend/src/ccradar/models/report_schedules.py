"""Reports sent by email on a schedule, as PDF or CSV."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from ccradar.db.base import Base, Timestamps, UUIDPrimaryKey
from ccradar.db.tenant import TenantMixin


class ReportSchedule(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "report_schedules"
    __audited__ = True
    __audit_ignore__ = frozenset({"last_sent_at", "last_error", "updated_at"})

    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(
        String(30)
    )  # cost_summary | savings | budgets | showback | leadership
    frequency: Mapped[str] = mapped_column(
        String(10), default="monthly"
    )  # daily | weekly | monthly
    weekday: Mapped[int] = mapped_column(Integer, default=0)  # weekly: 0 = Monday
    day: Mapped[int] = mapped_column(Integer, default=1)  # monthly: day of month (1-28)
    format: Mapped[str] = mapped_column(String(5), default="pdf")  # pdf | csv
    recipients: Mapped[list] = mapped_column(JSON, default=list)
    params: Mapped[dict] = mapped_column(JSON, default=dict)  # e.g. {"dimension": "tag:team"}
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))
