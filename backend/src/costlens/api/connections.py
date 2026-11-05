"""Cloud connections (AWS now; Azure and Google Cloud in section 7)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from costlens.accounts.roles import Permission
from costlens.api.deps import SessionDep, SettingsDep, require
from costlens.auth.web import AuthContext
from costlens.clouds.aws import policy as aws_policy
from costlens.clouds.aws.connections import create_aws_connection, verify_aws_connection
from costlens.core.errors import AppError, NotFoundError
from costlens.models import CloudConnection, CostImportRun

router = APIRouter(prefix="/api/v1/connections", tags=["connections"])
Reader = Annotated[AuthContext, require(Permission.READ)]
Admin = Annotated[AuthContext, require(Permission.MANAGE_CONNECTIONS)]
BUCKET = r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$"


class ConnectionOut(BaseModel):
    id: uuid.UUID
    provider: str
    name: str
    status: str
    external_id: str
    role_arn: str | None
    account_id: str | None
    cur_bucket: str | None
    cur_prefix: str | None
    last_checked_at: datetime | None
    last_error: str | None


class AwsCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    cur_bucket: str | None = Field(default=None, pattern=BUCKET)
    cur_prefix: str | None = Field(default=None, max_length=500)


class AwsVerify(BaseModel):
    role_arn: str = Field(min_length=20, max_length=600)


class ConnectionPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    # Format of the billing export in the bucket: CUR 2.0 (default) or FOCUS 1.0.
    export_format: Literal["cur2", "focus"] | None = None
    cur_bucket: str | None = Field(default=None, pattern=BUCKET)
    cur_prefix: str | None = Field(default=None, max_length=500)


class AwsSetup(BaseModel):
    external_id: str
    principal_arn: str
    role_name: str
    cloudformation: str
    terraform: str
    cli_command: str


def _get(db: SessionDep, connection_id: uuid.UUID) -> CloudConnection:
    conn = db.get(CloudConnection, connection_id)
    if conn is None:
        raise NotFoundError("Connection not found.")
    return conn


def _principal(settings: SettingsDep) -> str:
    if not settings.aws_principal_arn:
        raise AppError(
            "AWS connections are not configured on this installation (COSTLENS_AWS_PRINCIPAL_ARN).",
            status=503,
            code="aws_not_configured",
        )
    return settings.aws_principal_arn


@router.get("", response_model=list[ConnectionOut])
def list_connections(auth: Reader, db: SessionDep) -> list[ConnectionOut]:
    rows = db.scalars(select(CloudConnection).order_by(CloudConnection.created_at))
    return [ConnectionOut.model_validate(r, from_attributes=True) for r in rows]


@router.post("/aws", response_model=ConnectionOut, status_code=201)
def create_aws(
    body: AwsCreate, auth: Admin, db: SessionDep, settings: SettingsDep
) -> ConnectionOut:
    _principal(settings)
    assert auth.org_id is not None  # noqa: S101
    conn = create_aws_connection(db, org_id=auth.org_id, name=body.name)
    conn.cur_bucket, conn.cur_prefix = body.cur_bucket, body.cur_prefix
    db.flush()
    return ConnectionOut.model_validate(conn, from_attributes=True)


@router.get("/{connection_id}/aws-setup", response_model=AwsSetup)
def aws_setup(
    connection_id: uuid.UUID, auth: Admin, db: SessionDep, settings: SettingsDep
) -> AwsSetup:
    """Templates that create the read-only role for this connection."""
    conn = _get(db, connection_id)
    principal = _principal(settings)
    args = {
        "principal_arn": principal,
        "external_id": conn.external_id,
        "cur_bucket": conn.cur_bucket,
        "cur_prefix": conn.cur_prefix,
    }
    return AwsSetup(
        external_id=conn.external_id,
        principal_arn=principal,
        role_name=aws_policy.ROLE_NAME,
        cloudformation=aws_policy.cloudformation_template(**args),
        terraform=aws_policy.terraform_module(**args),
        cli_command=(
            "aws cloudformation deploy --stack-name costlens-read-only "
            "--template-file costlens-read-only.json --capabilities CAPABILITY_NAMED_IAM"
        ),
    )


@router.post("/{connection_id}/verify", response_model=ConnectionOut)
def verify(
    connection_id: uuid.UUID, body: AwsVerify, auth: Admin, db: SessionDep, request: Request
) -> ConnectionOut:
    conn = _get(db, connection_id)
    try:
        verify_aws_connection(db, request.app.state.aws, conn, body.role_arn)
    except AppError:
        db.commit()  # keep the error status for the UI
        raise
    return ConnectionOut.model_validate(conn, from_attributes=True)


@router.patch("/{connection_id}", response_model=ConnectionOut)
def update(
    connection_id: uuid.UUID, body: ConnectionPatch, auth: Admin, db: SessionDep
) -> ConnectionOut:
    conn = _get(db, connection_id)
    changes = body.model_dump(exclude_unset=True)
    export_format = changes.pop("export_format", None)
    if export_format:
        conn.settings = {**(conn.settings or {}), "export_format": export_format, "cur_etags": {}}
    for field, value in changes.items():
        setattr(conn, field, value)
    db.flush()
    return ConnectionOut.model_validate(conn, from_attributes=True)


@router.delete("/{connection_id}", status_code=204)
def delete(connection_id: uuid.UUID, auth: Admin, db: SessionDep) -> Response:
    """Stop collecting. Already imported cost data stays (delete it with retention)."""
    db.delete(_get(db, connection_id))
    db.flush()
    return Response(status_code=204)


class ImportRequest(BaseModel):
    months: int = Field(default=2, ge=1, le=13)


class ImportRunOut(BaseModel):
    id: uuid.UUID
    source_kind: str
    billing_period: date
    status: str
    rows: int
    total_cost: Decimal | None
    currency: str | None
    started_at: datetime
    finished_at: datetime | None
    error: str | None


@router.post("/{connection_id}/import", status_code=202)
def collect_now(
    connection_id: uuid.UUID, body: ImportRequest, auth: Admin, db: SessionDep, request: Request
) -> dict[str, str]:
    """Import costs now (Cost Explorer charges about USD 0.01 per request)."""
    conn = _get(db, connection_id)
    if conn.status != "active":
        raise AppError("Finish connecting this account first.", status=409, code="not_active")
    jobs = request.app.state.jobs
    if conn.cur_bucket:  # detailed billing export configured: use it
        jobs.enqueue("costs.import_cur", connection_id=str(conn.id), months=body.months, force=True)
        return {"status": "queued", "source": "aws_cur"}
    jobs.enqueue("costs.import_cost_explorer", connection_id=str(conn.id), months=body.months)
    return {"status": "queued", "source": "aws_ce"}


@router.get("/{connection_id}/imports", response_model=list[ImportRunOut])
def import_history(connection_id: uuid.UUID, auth: Reader, db: SessionDep) -> list[ImportRunOut]:
    conn = _get(db, connection_id)
    rows = db.scalars(
        select(CostImportRun)
        .where(CostImportRun.source_key.like(f"%:{conn.id}"))
        .order_by(CostImportRun.started_at.desc())
        .limit(30)
    )
    return [ImportRunOut.model_validate(r, from_attributes=True) for r in rows]


@router.post("/{connection_id}/permissions-check")
def check_permissions(
    connection_id: uuid.UUID, auth: Admin, db: SessionDep, request: Request
) -> dict[str, Any]:
    """Probe every read-only permission CostLens uses; results are kept on the connection."""
    from costlens.clouds.aws import permissions

    conn = _get(db, connection_id)
    if conn.status != "active":
        raise AppError("Finish connecting this account first.", status=409, code="not_active")
    report = permissions.run(request.app.state.aws, conn)
    conn.settings = {**(conn.settings or {}), "permissions": report}
    db.flush()
    return report


@router.get("/{connection_id}/permissions-check")
def last_permissions(connection_id: uuid.UUID, auth: Reader, db: SessionDep) -> dict[str, Any]:
    report = (_get(db, connection_id).settings or {}).get("permissions")
    if report is None:
        raise NotFoundError("No check has been run yet.")
    return report
