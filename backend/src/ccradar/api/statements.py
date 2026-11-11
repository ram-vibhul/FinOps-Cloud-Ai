"""Monthly statements, invoices, reconciliation and closing months."""

from __future__ import annotations

import csv
import io
import tempfile
import uuid
from collections.abc import Iterator
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Path, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from ccradar.accounts.roles import Permission
from ccradar.api.deps import SessionDep, require
from ccradar.auth.web import AuthContext
from ccradar.clouds.aws.client import AwsAccessError
from ccradar.core.errors import AppError, NotFoundError
from ccradar.costs import focus_export, statements
from ccradar.models import CloudConnection, Invoice

router = APIRouter(prefix="/api/v1/statements", tags=["statements"])
Reader = Annotated[AuthContext, require(Permission.READ)]
Admin = Annotated[AuthContext, require(Permission.MANAGE_SETTINGS)]
Month = Annotated[str, Path(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")]


def _month(value: str) -> date:
    y, m = value.split("-")
    return date(int(y), int(m), 1)


def _org(auth: AuthContext) -> uuid.UUID:
    assert auth.org_id is not None  # noqa: S101
    return auth.org_id


class CloseIn(BaseModel):
    note: str = Field(default="", max_length=1000)


class InvoiceIn(BaseModel):
    provider: Literal["aws", "azure", "gcp", "other"] = "aws"
    billing_account_id: str | None = Field(default=None, max_length=100)
    invoice_id: str = Field(min_length=1, max_length=200)
    amount: float = Field(ge=-1e12, le=1e12)
    currency: str = Field(default="USD", pattern="^[A-Z]{3}$")


@router.get("/{month}")
def get_statement(month: Month, auth: Reader, db: SessionDep) -> dict[str, Any]:
    return statements.statement(db, _org(auth), _month(month))


@router.get("/{month}/statement.csv")
def statement_csv(month: Month, auth: Reader, db: SessionDep) -> Response:
    s = statements.statement(db, _org(auth), _month(month))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["section", "item", "cost", "currency"])
    for section in ("by_category", "by_account", "by_service"):
        for row in s[section]:
            w.writerow(
                [section.removeprefix("by_"), row["key"], f"{row['cost']:.2f}", s["currency"]]
            )
    w.writerow(["total", "", f"{s['total']:.2f}", s["currency"]])
    for i in s["invoices"]:
        w.writerow(["invoice", i["invoice_id"], f"{i['amount']:.2f}", i["currency"]])
        w.writerow(["billed for invoice", i["invoice_id"], f"{i['billed']:.2f}", i["currency"]])
    return Response(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="statement-{month}.csv"'},
    )


@router.get("/{month}/focus.csv")
def focus_csv(month: Month, auth: Reader, db: SessionDep) -> StreamingResponse:
    """Every line item charged in the month, with FinOps FOCUS column names.

    Written to a temporary file (kept in memory up to 8 MB) while the database session
    is open, then streamed, so large months do not have to fit in memory."""
    out = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)  # noqa: SIM115
    for chunk in focus_export.stream(db, _org(auth), _month(month)):
        out.write(chunk)
    out.seek(0)

    def body() -> Iterator[bytes]:
        try:
            while chunk := out.read(256 * 1024):
                yield chunk
        finally:
            out.close()

    return StreamingResponse(
        body(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="focus-{month}.csv"'},
    )


@router.post("/{month}/close")
def close_month(month: Month, body: CloseIn, auth: Admin, db: SessionDep) -> dict[str, Any]:
    statements.close(db, _org(auth), _month(month), auth.user.id, body.note.strip())
    return statements.statement(db, _org(auth), _month(month))


@router.delete("/{month}/close")
def reopen_month(month: Month, auth: Admin, db: SessionDep) -> dict[str, Any]:
    statements.reopen(db, _month(month))
    return statements.statement(db, _org(auth), _month(month))


@router.post("/{month}/invoices", status_code=201)
def add_invoice(month: Month, body: InvoiceIn, auth: Admin, db: SessionDep) -> list[dict[str, Any]]:
    if db.scalar(
        select(Invoice.id).where(
            Invoice.provider == body.provider, Invoice.invoice_id == body.invoice_id
        )
    ):
        raise AppError("This invoice is already recorded.", code="duplicate_invoice")
    db.add(Invoice(org_id=auth.org_id, month=_month(month), source="manual", **body.model_dump()))
    db.flush()
    return statements.reconcile(db, _org(auth), _month(month))


@router.delete("/{month}/invoices/{invoice_id}", status_code=204)
def delete_invoice(month: Month, invoice_id: uuid.UUID, auth: Admin, db: SessionDep) -> Response:
    inv = db.get(Invoice, invoice_id)
    if inv is None:
        raise NotFoundError("Invoice not found.")
    db.delete(inv)
    db.flush()
    return Response(status_code=204)


@router.post("/{month}/invoices/fetch")
def fetch_invoices(month: Month, auth: Admin, db: SessionDep, request: Request) -> dict[str, Any]:
    """Read invoice totals from AWS (invoicing:ListInvoiceSummaries) for each connection."""
    gateway = request.app.state.aws
    added, errors = 0, []
    for conn in db.scalars(
        select(CloudConnection).where(
            CloudConnection.provider == "aws", CloudConnection.status == "active"
        )
    ):
        if not conn.role_arn or not conn.account_id:
            continue
        try:
            session = gateway.assume(conn.role_arn, conn.external_id, f"ccradar-{conn.org_id}")
            found = statements.fetch_aws_invoices(
                gateway.client(session, "invoicing", region="us-east-1"),
                conn.account_id,
                _month(month),
            )
        except (AwsAccessError, Exception) as error:
            errors.append(f"{conn.name}: {type(error).__name__}")
            continue
        for i in found:
            if db.scalar(
                select(Invoice.id).where(
                    Invoice.provider == "aws", Invoice.invoice_id == i["invoice_id"]
                )
            ):
                continue
            db.add(
                Invoice(org_id=auth.org_id, provider="aws", month=_month(month), source="aws", **i)
            )
            added += 1
    db.flush()
    return {
        "added": added,
        "errors": errors,
        "invoices": statements.reconcile(db, _org(auth), _month(month)),
    }
