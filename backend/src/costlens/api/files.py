"""Upload and import billing files (CSV or Parquet; FOCUS or custom)."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from costlens.accounts.roles import Permission
from costlens.api.deps import SessionDep, require
from costlens.auth.web import AuthContext
from costlens.core.errors import AppError, NotFoundError
from costlens.costs import files, focus
from costlens.models import FileImport

router = APIRouter(prefix="/api/v1/imports/files", tags=["file imports"])
Reader = Annotated[AuthContext, require(Permission.READ)]
Admin = Annotated[AuthContext, require(Permission.MANAGE_CONNECTIONS)]
SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class FileImportOut(BaseModel):
    id: uuid.UUID
    filename: str
    size_bytes: int
    file_type: str
    detected_format: str
    status: str
    columns: list[str]
    mapping: dict[str, Any]
    options: dict[str, Any]
    report: dict[str, Any]
    rows_imported: int
    created_at: datetime
    imported_at: datetime | None


class Preview(BaseModel):
    columns: list[str]
    sample: list[dict[str, Any]]
    detected_format: str
    suggested_mapping: dict[str, str]
    fields: dict[str, str]


class ImportSettings(BaseModel):
    mapping: dict[str, Any] = Field(default_factory=dict)
    provider: str = Field(default="other", pattern=r"^[a-z0-9_]{1,30}$")
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    date_format: str | None = Field(default=None, max_length=40)
    allow_partial: bool = False


def _get(db: SessionDep, import_id: uuid.UUID) -> FileImport:
    record = db.get(FileImport, import_id)
    if record is None:
        raise NotFoundError("Upload not found.")
    return record


def _out(r: FileImport) -> FileImportOut:
    return FileImportOut.model_validate(r, from_attributes=True)


@router.post("", response_model=FileImportOut, status_code=201)
async def upload(
    request: Request,
    auth: Admin,
    db: SessionDep,
    filename: Annotated[str, Query(min_length=1, max_length=255)],
) -> FileImportOut:
    """Send the file as the raw request body (CSV or Parquet, up to 500 MB)."""
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > files.MAX_UPLOAD_BYTES:
            raise AppError("The file is larger than 500 MB.", status=413, code="too_large")
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise AppError("The file is empty.", code="empty_file")
    file_type = files.sniff_type(filename, data[:4])
    record = FileImport(
        org_id=auth.org_id,
        filename=filename,
        size_bytes=size,
        file_type=file_type,
        detected_format="custom",
        status="uploaded",
        uploaded_by_id=auth.user.id,
    )
    db.add(record)
    db.flush()
    key = f"uploads/{auth.org_id}/{record.id}/{SAFE_NAME.sub('_', filename)[:100]}"
    storage = request.app.state.storage
    storage.put(key, data)
    record.storage_key = key
    try:
        columns, _ = files.read_columns(storage, key, file_type)
    except Exception as exc:
        storage.delete(key)
        raise AppError(
            "The file could not be read as CSV or Parquet.", code="unreadable_file"
        ) from exc
    record.columns = columns
    record.detected_format = "focus" if focus.is_focus(columns) else "custom"
    record.mapping = {} if record.detected_format == "focus" else files.suggest_mapping(columns)
    db.flush()
    return _out(record)


@router.get("", response_model=list[FileImportOut])
def list_uploads(auth: Reader, db: SessionDep) -> list[FileImportOut]:
    rows = db.scalars(select(FileImport).order_by(FileImport.created_at.desc()).limit(50))
    return [_out(r) for r in rows]


@router.get("/{import_id}/preview", response_model=Preview)
def preview(import_id: uuid.UUID, request: Request, auth: Admin, db: SessionDep) -> Preview:
    record = _get(db, import_id)
    columns, sample = files.read_columns(
        request.app.state.storage, record.storage_key or "", record.file_type
    )
    return Preview(
        columns=columns,
        sample=[{k: str(v) for k, v in r.items()} for r in sample],
        detected_format=record.detected_format,
        suggested_mapping=files.suggest_mapping(columns),
        fields=files.CUSTOM_FIELDS,
    )


def _apply(record: FileImport, body: ImportSettings) -> files.Options:
    opts = files.Options(
        provider=body.provider, currency=body.currency, date_format=body.date_format
    )
    mapping = body.mapping or record.mapping or {}
    unknown = [v for k, v in mapping.items() if k != "_tag_columns" and v not in record.columns]
    unknown += [c for c in mapping.get("_tag_columns", []) if c not in record.columns]
    if unknown:
        raise AppError(
            "These columns are not in the file: " + ", ".join(map(str, unknown)),
            code="unknown_columns",
        )
    files.check_mapping(record.detected_format, mapping, opts)
    record.mapping = mapping
    record.options = {
        "provider": body.provider,
        "currency": body.currency,
        "date_format": body.date_format,
        "allow_partial": body.allow_partial,
    }
    return opts


@router.post("/{import_id}/validate")
def validate(
    import_id: uuid.UUID, body: ImportSettings, request: Request, auth: Admin, db: SessionDep
) -> dict[str, Any]:
    """Dry run on the first 10,000 rows: what would be imported, and every problem."""
    record = _get(db, import_id)
    opts = _apply(record, body)
    rows = files.iter_rows(request.app.state.storage, record.storage_key or "", record.file_type)
    _, report = files.convert(
        rows,
        detected=record.detected_format,
        mapping=record.mapping,
        opts=opts,
        limit=files.DRY_RUN_ROWS,
    )
    db.flush()
    return {**report.as_dict(), "sampled": report.rows_read >= files.DRY_RUN_ROWS}


@router.post("/{import_id}/import", response_model=FileImportOut, status_code=202)
def start_import(
    import_id: uuid.UUID, body: ImportSettings, request: Request, auth: Admin, db: SessionDep
) -> FileImportOut:
    record = _get(db, import_id)
    _apply(record, body)
    record.status = "queued"
    db.commit()
    request.app.state.jobs.enqueue("costs.import_file", import_id=str(record.id))
    db.refresh(record)
    return _out(record)


@router.delete("/{import_id}", status_code=204)
def delete_upload(import_id: uuid.UUID, request: Request, auth: Admin, db: SessionDep) -> Response:
    """Delete the upload and the costs it imported."""
    from sqlalchemy import delete

    from costlens.costs.ingest import refresh_daily
    from costlens.db.base import Base

    record = _get(db, import_id)
    table = Base.metadata.tables["cost_line_items"]
    source = f"file:{record.id}"
    first_last = (
        db.execute(
            select(table.c.charge_date)
            .where(table.c.org_id == record.org_id, table.c.source_key == source)
            .order_by(table.c.charge_date)
        )
        .scalars()
        .all()
    )
    db.execute(delete(table).where(table.c.org_id == record.org_id, table.c.source_key == source))
    if first_last:
        refresh_daily(db, org_id=record.org_id, start=first_last[0], end=first_last[-1])
    if record.storage_key:
        request.app.state.storage.delete(record.storage_key)
    db.delete(record)
    db.flush()
    return Response(status_code=204)
