"""Savings findings from every detector, in one ranked list."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from ccradar.db.base import Base, Timestamps, UUIDPrimaryKey
from ccradar.db.tenant import TenantMixin


class Finding(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "findings"
    __audited__ = True
    # Detectors refresh these on every run; only status changes are worth an audit entry.
    __audit_ignore__ = frozenset(
        {
            "last_seen_at",
            "savings_monthly",
            "evidence",
            "title",
            "description",
            "steps",
            "commands",
            "risk",
            "effort",
            "confidence",
            "team",
            "owner_source",
            "currency",
            "resolved_at",
        }
    )
    __table_args__ = (UniqueConstraint("org_id", "dedup_key", name="uq_findings_dedup"),)

    detector: Mapped[str] = mapped_column(String(80), index=True)
    category: Mapped[str] = mapped_column(
        String(30)
    )  # waste | rightsizing | commitments | network | kubernetes | database
    dedup_key: Mapped[str] = mapped_column(String(300))
    overlap_key: Mapped[str] = mapped_column(String(300))  # same resource: never counted twice
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(String(2000), default="")
    provider: Mapped[str | None] = mapped_column(String(30))
    account_id: Mapped[str | None] = mapped_column(String(100))
    region: Mapped[str | None] = mapped_column(String(60))
    service: Mapped[str | None] = mapped_column(String(200))
    resource_type: Mapped[str | None] = mapped_column(String(60))
    resource_id: Mapped[str | None] = mapped_column(String(1000))
    savings_monthly: Mapped[float] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3))
    risk: Mapped[str] = mapped_column(String(10))  # low | medium | high
    effort: Mapped[str] = mapped_column(String(10))  # low | medium | high
    confidence: Mapped[str] = mapped_column(String(10), default="medium")  # high | medium | low
    steps: Mapped[list] = mapped_column(JSON, default=list)
    commands: Mapped[list] = mapped_column(JSON, default=list)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    # open -> approved -> applied -> verified; or dismissed; gone = the detector no longer sees it
    status: Mapped[str] = mapped_column(String(20), default="open")
    status_note: Mapped[str | None] = mapped_column(String(1000))
    status_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    owner_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    team: Mapped[str | None] = mapped_column(String(200))
    owner_source: Mapped[str | None] = mapped_column(String(10))  # rule | person
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    realised_monthly: Mapped[float | None] = mapped_column(Numeric(14, 2))  # measured from the bill
    measured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    measurement: Mapped[dict] = mapped_column(JSON, default=dict)


class FindingEvent(TenantMixin, UUIDPrimaryKey, Base):
    """A finding's history: status and owner changes, comments, measurements."""

    __tablename__ = "finding_events"

    finding_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("findings.id", ondelete="CASCADE"), index=True
    )
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # None = CloudCostRadar itself
    kind: Mapped[str] = mapped_column(String(20))  # status | owner | comment | measured
    text: Mapped[str] = mapped_column(String(2000), default="")
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class Suppression(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    """A "doesn't apply to us" rule: matching findings are dismissed automatically, with
    the reason, until the rule is removed (then they reopen)."""

    __tablename__ = "finding_suppressions"
    __audited__ = True

    detector: Mapped[str] = mapped_column(String(80), default="*")  # "*" = every check
    # finding | resource | tag | account | check
    scope: Mapped[str] = mapped_column(String(20))
    value: Mapped[str] = mapped_column(
        String(1000), default=""
    )  # dedup key, resource id, key=value, account
    reason: Mapped[str] = mapped_column(
        String(30)
    )  # not_applicable | accepted_risk | false_positive | planned | other
    note: Mapped[str] = mapped_column(String(1000), default="")
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OwnerRule(TenantMixin, UUIDPrimaryKey, Timestamps, Base):
    """Routes findings to a team and/or person. Lowest priority number wins; a person
    assigning an owner by hand always wins over rules."""

    __tablename__ = "finding_owner_rules"
    __audited__ = True

    name: Mapped[str] = mapped_column(String(100))
    priority: Mapped[int] = mapped_column(Integer, default=100)
    # account | tag | vtag | service | namespace | category
    kind: Mapped[str] = mapped_column(String(20))
    key: Mapped[str] = mapped_column(String(200), default="")  # tag / virtual tag key
    value: Mapped[str] = mapped_column(String(500))
    team: Mapped[str | None] = mapped_column(String(200))
    owner_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
