"""Monthly statements, invoice reconciliation and closed-month locking.

A statement totals the month by charge category, account and service. Each provider
invoice is compared with the billed cost of its billing account (same currency): within
1 unit or 0.5% it matches. Closing a month freezes its statement and locks the month
against re-imports until it is reopened."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from costlens.core.errors import AppError
from costlens.costs.fx import converted
from costlens.db.base import Base, utcnow
from costlens.models import Invoice, MonthClose, Organization

TOP = 15
TOLERANCE_PCT, TOLERANCE_ABS = 0.5, 1.0


def _next(month: date) -> date:
    return date(month.year + (month.month == 12), month.month % 12 + 1, 1)


def _grouped(
    db: Session, org_id: uuid.UUID, month: date, currency: str, column: str
) -> list[tuple[str, float]]:
    t = Base.metadata.tables["cost_daily"]
    amount = converted(t.c.billed_cost, t.c.billing_currency, t.c.charge_date, currency, t.c.org_id)
    rows = db.execute(
        select(t.c[column], func.sum(amount))
        .where(t.c.org_id == org_id, t.c.charge_date >= month, t.c.charge_date < _next(month))
        .group_by(t.c[column])
        .order_by(func.sum(amount).desc())
    ).all()
    return [(str(k or "(none)"), round(float(v or 0), 2)) for k, v in rows]


def reconcile(db: Session, org_id: uuid.UUID, month: date) -> list[dict[str, Any]]:
    t = Base.metadata.tables["cost_line_items"]
    out = []
    for inv in db.scalars(select(Invoice).where(Invoice.month == month)):
        q = select(func.sum(t.c.billed_cost)).where(
            t.c.org_id == org_id,
            t.c.billing_period == month,
            t.c.billing_currency == inv.currency,
            t.c.provider == inv.provider,
        )
        if inv.billing_account_id:
            q = q.where(t.c.billing_account_id == inv.billing_account_id)
        billed = float(db.scalar(q) or 0)
        diff = round(inv.amount - billed, 2)
        ok = abs(diff) <= max(TOLERANCE_ABS, abs(inv.amount) * TOLERANCE_PCT / 100)
        out.append(
            {
                "id": str(inv.id),
                "invoice_id": inv.invoice_id,
                "provider": inv.provider,
                "billing_account_id": inv.billing_account_id,
                "amount": inv.amount,
                "currency": inv.currency,
                "billed": round(billed, 2),
                "difference": diff,
                "status": "matched" if ok else "mismatch",
                "source": inv.source,
            }
        )
    return out


def build(db: Session, org_id: uuid.UUID, month: date) -> dict[str, Any]:
    org = db.get(Organization, org_id)
    currency = org.default_currency if org else "USD"
    categories = _grouped(db, org_id, month, currency, "charge_category")
    accounts = _grouped(db, org_id, month, currency, "sub_account_id")
    services = _grouped(db, org_id, month, currency, "service_name")
    return {
        "month": month.strftime("%Y-%m"),
        "currency": currency,
        "total": round(sum(v for _, v in categories), 2),
        "by_category": [{"key": k, "cost": v} for k, v in categories],
        "by_account": [{"key": k, "cost": v} for k, v in accounts[:TOP]],
        "by_service": [{"key": k, "cost": v} for k, v in services[:TOP]],
        "invoices": reconcile(db, org_id, month),
    }


def statement(db: Session, org_id: uuid.UUID, month: date) -> dict[str, Any]:
    closed = db.scalar(select(MonthClose).where(MonthClose.month == month))
    if closed is not None:
        frozen = dict(closed.snapshot)
        frozen["invoices"] = reconcile(db, org_id, month)  # invoices may still arrive after closing
        return {**frozen, "closed": {"at": closed.closed_at.isoformat(), "note": closed.note}}
    return {**build(db, org_id, month), "closed": None}


def close(
    db: Session, org_id: uuid.UUID, month: date, user_id: uuid.UUID | None, note: str
) -> MonthClose:
    if month >= utcnow().date().replace(day=1):
        raise AppError("Only past months can be closed.", code="month_not_over")
    if db.scalar(select(MonthClose).where(MonthClose.month == month)) is not None:
        raise AppError("This month is already closed.", code="already_closed")
    row = MonthClose(
        org_id=org_id,
        month=month,
        closed_at=utcnow(),
        closed_by_id=user_id,
        note=note,
        snapshot=build(db, org_id, month),
    )
    db.add(row)
    db.flush()
    return row


def reopen(db: Session, month: date) -> None:
    row = db.scalar(select(MonthClose).where(MonthClose.month == month))
    if row is None:
        raise AppError("This month is not closed.", code="not_closed")
    db.delete(row)
    db.flush()


def fetch_aws_invoices(client: Any, account_id: str, month: date) -> list[dict[str, Any]]:
    """AWS Invoicing ``ListInvoiceSummaries`` for one account and billing month (read-only)."""
    out = []
    token = None
    for _ in range(20):
        args: dict[str, Any] = {
            "Selector": {"ResourceType": "ACCOUNT_ID", "Value": account_id},
            "Filter": {"BillingPeriod": {"Month": month.month, "Year": month.year}},
        }
        if token:
            args["NextToken"] = token
        page = client.list_invoice_summaries(**args)
        for s in page.get("InvoiceSummaries", []):
            amount = s.get("BaseCurrencyAmount") or {}
            out.append(
                {
                    "invoice_id": s.get("InvoiceId"),
                    "billing_account_id": s.get("AccountId") or account_id,
                    "amount": float(amount.get("TotalAmount") or 0),
                    "currency": amount.get("CurrencyCode") or "USD",
                }
            )
        token = page.get("NextToken")
        if not token:
            break
    return [i for i in out if i["invoice_id"]]
