""" "What you kept": verified savings rechecked on the bill every month.

For each verified finding, last month's average daily cost of the resource is compared
with the daily cost before the change (measured when it was verified). If less than half
of the confirmed saving is still there, the saving has come back: the finding reopens,
its history says so, and subscribed channels hear about it."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from costlens.core.notify import Message, notify
from costlens.db.base import utcnow
from costlens.findings import workflow
from costlens.models import Finding, Organization

MONTH = 30.4
REGRESSION_SHARE = 0.5


def _month(today: date) -> tuple[date, date, str]:
    end = today.replace(day=1) - timedelta(days=1)
    start = end.replace(day=1)
    return start, end, start.strftime("%Y-%m")


def recheck(db: Session, today: date | None = None) -> dict[str, int]:
    today = today or utcnow().date()
    start, end, label = _month(today)
    counts = {"checked": 0, "regressed": 0}
    messages = []
    for f in db.scalars(select(Finding).where(Finding.status == "verified")):
        m = dict(f.measurement or {})
        before = m.get("before_daily")
        confirmed = float(f.realised_monthly or 0)
        if before is None or confirmed <= 0 or not f.resource_id:
            continue
        if any(h.get("month") == label for h in m.get("history", [])):
            continue
        if f.verified_at and f.verified_at.date() > end:
            continue  # verified after that month: nothing to recheck yet
        daily = workflow._daily_k8s if f.provider == "kubernetes" else workflow._daily_aws
        org = db.get(Organization, f.org_id)
        currency = org.default_currency if org else f.currency
        costs = daily(
            db, f, currency,
            datetime.combine(start, datetime.min.time(), UTC),
            datetime.combine(end + timedelta(days=1), datetime.min.time(), UTC),
        )  # fmt: skip
        days = (end - start).days + 1
        avg = sum(costs.values()) / days  # missing days: the resource cost nothing
        kept = round((float(before) - avg) * MONTH, 2)
        m["history"] = [
            *m.get("history", []),
            {"month": label, "kept_monthly": kept, "daily": round(avg, 4)},
        ]
        counts["checked"] += 1
        if kept < REGRESSION_SHARE * confirmed:
            m["regressed_at"] = utcnow().isoformat()
            f.measurement = m
            note = f"The saving came back in {label}: {kept:,.2f} of {confirmed:,.2f} kept."
            f.status, f.status_note = "open", note
            f.verified_at = None
            workflow.event(
                db,
                f,
                "status",
                None,
                note,
                {"from": "verified", "to": "open", "regressed": True},
            )
            counts["regressed"] += 1
            messages.append(
                Message(
                    title=f"Saving came back: {f.title}",
                    text=f"In {label} only {kept:,.2f} {currency} of the confirmed {confirmed:,.2f} a month was kept. "
                    "The finding is open again in CostLens.",
                    link="/savings",
                )
            )
        else:
            f.measurement = m
    db.flush()
    if messages:
        notify(db, "saving_regressed", messages)
    return counts


def summary(db: Session) -> dict[str, Any]:
    """Kept per month (latest 12) and recent regressions."""
    by_month: dict[str, float] = {}
    best: dict[tuple[str, str], float] = {}
    regressions = []
    currency = None
    for f in db.scalars(select(Finding).where(Finding.status.in_(("verified", "open")))):
        m = f.measurement or {}
        currency = currency or m.get("currency") or f.currency
        for h in m.get("history", []):
            key = (h["month"], f.overlap_key)
            best[key] = max(best.get(key, float("-inf")), float(h["kept_monthly"]))
        if m.get("regressed_at") and f.status == "open":
            regressions.append(
                {
                    "id": str(f.id),
                    "title": f.title,
                    "regressed_at": m["regressed_at"],
                    "history": m.get("history", [])[-3:],
                }
            )
    for (month, _), kept in best.items():
        by_month[month] = by_month.get(month, 0.0) + max(kept, 0.0)
    months = sorted(by_month)[-12:]
    latest = months[-1] if months else None
    return {
        "currency": currency,
        "latest_month": latest,
        "kept_latest_month": round(by_month[latest], 2) if latest else 0.0,
        "kept_total": round(sum(by_month[mo] for mo in months), 2),
        "months": [{"month": mo, "kept": round(by_month[mo], 2)} for mo in months],
        "regressions": sorted(regressions, key=lambda r: r["regressed_at"], reverse=True)[:20],
    }
