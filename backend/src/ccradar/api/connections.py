"""Cloud connections (AWS now; Azure and Google Cloud in section 7)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ccradar.accounts.roles import Permission
from ccradar.api.deps import SessionDep, SettingsDep, require
from ccradar.auth.web import AuthContext
from ccradar.clouds.aws import policy as aws_policy
from ccradar.clouds.aws.connections import create_aws_connection, verify_aws_connection
from ccradar.config import Settings
from ccradar.core.errors import AppError, NotFoundError
from ccradar.db.base import utcnow
from ccradar.models import CloudConnection, CostImportRun, Organization

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
    azure: dict[str, str] | None = None  # scope, tenant, client and export (never the secret)
    gcp: dict[str, str] | None = None  # service account email, table, query project
    saas: dict[str, str] | None = None  # non-secret settings of a SaaS cost connector
    bedrock_logs: dict[str, str] | None = None  # Bedrock invocation log bucket and last read
    export_check: dict[str, Any] | None = None  # the last live billing-export check (9.5)


AZURE_PUBLIC = ("tenant_id", "client_id", "scope", "storage_account", "container", "prefix")


def _room_for_one_more(db: Session, settings: Settings, org_id: uuid.UUID) -> None:
    """Plan limit on connections (B.1)."""
    from ccradar.billing.usage import enforce

    org = db.get(Organization, org_id)
    if org is not None:
        enforce(db, org, settings, "connections")


def _start_first_run(db: Session, request: Request, conn: CloudConnection) -> bool:
    """The organisation's first connection: import, advice and detectors straight away so
    the first savings show within minutes (9.5). Later connections import as usual."""
    from ccradar.findings.first_savings import mark

    org = db.get(Organization, conn.org_id)
    if org is None or org.is_demo or (org.settings or {}).get("first_run"):
        return False
    import ccradar.jobs.onboarding  # noqa: F401 - registers the job in this process

    mark(org, "running", connection_id=str(conn.id))
    db.commit()  # the job reads the connection from its own session
    request.app.state.jobs.enqueue("onboarding.first_run", connection_id=str(conn.id))
    return True


def _out(conn: CloudConnection) -> ConnectionOut:
    out = ConnectionOut.model_validate(conn, from_attributes=True)
    cfg = conn.settings or {}
    if conn.provider == "aws" and (cfg.get("bedrock_logs") or {}).get("bucket"):
        b = cfg["bedrock_logs"]
        out.bedrock_logs = {
            k: str(b[k])
            for k in ("bucket", "prefix", "read_at", "events", "error")
            if b.get(k) is not None
        }
    if conn.provider in ("aws", "azure"):
        out.export_check = cfg.get("export_check")
    if conn.provider == "azure":
        out.azure = {k: str(cfg[k]) for k in AZURE_PUBLIC if cfg.get(k)}
    elif conn.provider not in ("aws", "gcp"):
        out.saas = {k: str(v) for k, v in cfg.items() if not k.startswith("_")}
    if conn.provider == "gcp":
        out.gcp = {
            k: str(cfg[k])
            for k in ("client_email", "table", "query_project", "detailed")
            if k in cfg
        }
    return out


class SaasCreate(BaseModel):
    provider: Literal["snowflake", "databricks", "datadog", "mongodb_atlas"]
    name: str = Field(min_length=1, max_length=200)
    settings: dict[str, str] = Field(default_factory=dict)
    secret: dict[str, str] = Field(default_factory=dict)


class GcpCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    service_account_key: str = Field(min_length=100, max_length=5000)
    table: str = Field(max_length=300)  # project.dataset.table of the billing export
    query_project: str | None = Field(default=None, max_length=30)
    detailed: bool = False  # the detailed (resource-level) export table


class AzureCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    tenant_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    client_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    client_secret: str = Field(min_length=8, max_length=1000)
    scope: str = Field(max_length=300)
    storage_account: str | None = Field(default=None, pattern=r"^[a-z0-9]{3,24}$")
    container: str | None = Field(default=None, pattern=r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?$")
    prefix: str | None = Field(default=None, max_length=500)


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
    # Bedrock model invocation logs in S3 (7.6); empty bucket turns it off.
    bedrock_log_bucket: str | None = Field(default=None, pattern=r"^$|" + BUCKET)
    bedrock_log_prefix: str | None = Field(default=None, max_length=500)
    bedrock_rules: list[dict[str, str]] | None = Field(default=None, max_length=100)


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
            "AWS connections are not configured on this installation (CCRADAR_AWS_PRINCIPAL_ARN).",
            status=503,
            code="aws_not_configured",
        )
    return settings.aws_principal_arn


@router.get("", response_model=list[ConnectionOut])
def list_connections(auth: Reader, db: SessionDep) -> list[ConnectionOut]:
    rows = db.scalars(select(CloudConnection).order_by(CloudConnection.created_at))
    return [_out(r) for r in rows]


@router.post("/aws", response_model=ConnectionOut, status_code=201)
def create_aws(
    body: AwsCreate, auth: Admin, db: SessionDep, settings: SettingsDep
) -> ConnectionOut:
    _principal(settings)
    assert auth.org_id is not None  # noqa: S101
    _room_for_one_more(db, settings, auth.org_id)
    conn = create_aws_connection(db, org_id=auth.org_id, name=body.name)
    conn.cur_bucket, conn.cur_prefix = body.cur_bucket, body.cur_prefix
    db.flush()
    return _out(conn)


@router.post("/azure", response_model=ConnectionOut, status_code=201)
def create_azure(body: AzureCreate, request: Request, auth: Admin, db: SessionDep) -> ConnectionOut:
    """Check the service principal can sign in and read the scope, then save it."""
    from ccradar.clouds.azure import costs as azure_costs
    from ccradar.clouds.azure.client import AzureClient, AzureError

    assert auth.org_id is not None  # noqa: S101
    _room_for_one_more(db, request.app.state.settings, auth.org_id)
    scope = "/" + body.scope.strip().strip("/")
    if not azure_costs.SCOPE.match(scope):
        raise AppError(
            "Use a subscription (/subscriptions/<id>), billing account or management group scope.",
            code="bad_scope",
        )
    if bool(body.storage_account) != bool(body.container):
        raise AppError("Give both the storage account and the container.", code="bad_export")
    client = AzureClient(request.app.state.http, body.tenant_id, body.client_id, body.client_secret)
    try:
        client.token()
    except AzureError as exc:
        raise AppError(f"Azure sign-in failed: {exc}", code="azure_sign_in_failed") from exc
    conn = CloudConnection(
        org_id=auth.org_id,
        provider="azure",
        name=body.name.strip(),
        status="active",
        external_id=f"azure-{uuid.uuid4().hex}",
        account_id=scope.split("/")[2] if scope.startswith("/subscriptions/") else None,
        secret=body.client_secret,
        settings={
            "tenant_id": body.tenant_id,
            "client_id": body.client_id,
            "scope": scope,
            **(
                {"storage_account": body.storage_account, "container": body.container}
                if body.storage_account
                else {}
            ),
            **({"prefix": body.prefix.strip("/")} if body.prefix else {}),
        },
    )
    db.add(conn)
    db.flush()
    if not _start_first_run(db, request, conn):
        request.app.state.jobs.enqueue("costs.import_azure", connection_id=str(conn.id), months=3)
    return _out(conn)


@router.post("/saas", response_model=ConnectionOut, status_code=201)
def create_saas(body: SaasCreate, request: Request, auth: Admin, db: SessionDep) -> ConnectionOut:
    """Snowflake, Databricks, Datadog or MongoDB Atlas. Checked by reading this month's cost."""
    import json

    from ccradar.connectors.saas import CONNECTORS, OPTIONAL, ConnectorError

    assert auth.org_id is not None  # noqa: S101
    _room_for_one_more(db, request.app.state.settings, auth.org_id)
    spec = CONNECTORS[body.provider]
    allowed = set(spec.settings) | set(OPTIONAL.get(body.provider, ()))
    settings = {k: v.strip() for k, v in body.settings.items() if k in allowed and v.strip()}
    secret = {k: v for k, v in body.secret.items() if k in spec.secrets and v}
    missing = [k for k in spec.settings if k not in settings] + [
        k for k in spec.secrets if k not in secret
    ]
    if missing:
        raise AppError(f"Missing: {', '.join(missing)}.", code="missing_fields")
    try:
        spec.fetch(request.app.state.http, settings, secret, utcnow().date().replace(day=1))
    except (ConnectorError, KeyError, ValueError) as exc:
        raise AppError(f"{spec.label} check failed: {exc}", code="connector_check_failed") from exc
    conn = CloudConnection(
        org_id=auth.org_id,
        provider=body.provider,
        name=body.name.strip(),
        status="active",
        external_id=f"{body.provider}-{uuid.uuid4().hex}",
        secret=json.dumps(secret),
        settings=settings,
    )
    db.add(conn)
    db.flush()
    if not _start_first_run(db, request, conn):
        request.app.state.jobs.enqueue("costs.import_saas", connection_id=str(conn.id), months=3)
    return _out(conn)


@router.post("/gcp", response_model=ConnectionOut, status_code=201)
def create_gcp(body: GcpCreate, request: Request, auth: Admin, db: SessionDep) -> ConnectionOut:
    """Check the service account key can sign in, then save it with the export table."""
    from ccradar.clouds.gcp import costs as gcp_costs
    from ccradar.clouds.gcp.client import GcpClient, GcpError, parse_key

    assert auth.org_id is not None  # noqa: S101
    _room_for_one_more(db, request.app.state.settings, auth.org_id)
    try:
        key = parse_key(body.service_account_key)
    except GcpError as exc:
        raise AppError(f"Service account key: {exc}.", code="bad_key") from exc
    table = body.table.strip().replace(":", ".")
    if not gcp_costs.TABLE.match(table):
        raise AppError("The table must look like project.dataset.table.", code="bad_table")
    project = (body.query_project or table.split(".")[0]).strip()
    if not gcp_costs.PROJECT.match(project):
        raise AppError("Not a Google Cloud project ID.", code="bad_project")
    try:
        GcpClient(request.app.state.http, key).token()
    except GcpError as exc:
        raise AppError(f"Google sign-in failed: {exc}", code="gcp_sign_in_failed") from exc
    conn = CloudConnection(
        org_id=auth.org_id,
        provider="gcp",
        name=body.name.strip(),
        status="active",
        external_id=f"gcp-{uuid.uuid4().hex}",
        account_id=key["project_id"][:64],
        secret=body.service_account_key,
        settings={
            "client_email": key["client_email"],
            "table": table,
            "query_project": project,
            "detailed": body.detailed,
        },
    )
    db.add(conn)
    db.flush()
    if not _start_first_run(db, request, conn):
        request.app.state.jobs.enqueue("costs.import_gcp", connection_id=str(conn.id), months=3)
    return _out(conn)


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
        "bedrock_bucket": ((conn.settings or {}).get("bedrock_logs") or {}).get("bucket"),
        "bedrock_prefix": ((conn.settings or {}).get("bedrock_logs") or {}).get("prefix"),
    }
    return AwsSetup(
        external_id=conn.external_id,
        principal_arn=principal,
        role_name=aws_policy.ROLE_NAME,
        cloudformation=aws_policy.cloudformation_template(**args),
        terraform=aws_policy.terraform_module(**args),
        cli_command=(
            "aws cloudformation deploy --stack-name ccradar-read-only "
            "--template-file ccradar-read-only.json --capabilities CAPABILITY_NAMED_IAM"
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
    if not _start_first_run(db, request, conn):
        db.commit()
        request.app.state.jobs.enqueue("costs.import_cost_explorer", connection_id=str(conn.id))
    return _out(conn)


@router.patch("/{connection_id}", response_model=ConnectionOut)
def update(
    connection_id: uuid.UUID, body: ConnectionPatch, auth: Admin, db: SessionDep
) -> ConnectionOut:
    conn = _get(db, connection_id)
    changes = body.model_dump(exclude_unset=True)
    export_format = changes.pop("export_format", None)
    bedrock = {
        k: changes.pop(k)
        for k in ("bedrock_log_bucket", "bedrock_log_prefix", "bedrock_rules")
        if k in changes
    }
    if bedrock:
        if conn.provider != "aws":
            raise AppError("Bedrock logs are read from AWS connections only.", code="not_aws")
        current = dict((conn.settings or {}).get("bedrock_logs") or {})
        if "bedrock_log_bucket" in bedrock:
            current["bucket"] = bedrock["bedrock_log_bucket"] or None
        if "bedrock_log_prefix" in bedrock:
            current["prefix"] = (bedrock["bedrock_log_prefix"] or "").strip("/") or None
        if "bedrock_rules" in bedrock:
            current["rules"] = [
                {k: str(r.get(k, ""))[:200] for k in ("match", "app", "team")}
                for r in bedrock["bedrock_rules"] or []
                if r.get("match")
            ]
        conn.settings = {**(conn.settings or {}), "bedrock_logs": current}
    if export_format:
        conn.settings = {**(conn.settings or {}), "export_format": export_format, "cur_etags": {}}
    for field, value in changes.items():
        setattr(conn, field, value)
    db.flush()
    return _out(conn)


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


@router.get("/setup-help/{provider}")
def setup_help(
    provider: Literal["azure", "gcp"],
    auth: Reader,
    scope: str | None = None,
    storage_account: str | None = None,
    container: str | None = None,
    resource_group: str | None = None,
    table: str | None = None,
    query_project: str | None = None,
) -> dict[str, Any]:
    """The read-only roles to grant, as commands, before connecting."""
    from ccradar.clouds import setup_help as help_

    s = help_.safe
    if provider == "azure":
        return help_.azure(s(scope), s(storage_account), s(container), s(resource_group))
    return help_.gcp(s(table), s(query_project))


@router.get("/{connection_id}/setup-help")
def connection_setup_help(connection_id: uuid.UUID, auth: Reader, db: SessionDep) -> dict[str, Any]:
    """Roles and commands for an existing Azure or Google Cloud connection, plus what the
    last collections saw: billing months, rows, the date range of the data and errors."""
    from sqlalchemy import func

    from ccradar.clouds import setup_help as help_
    from ccradar.models import CostLineItem

    conn = _get(db, connection_id)
    cfg = conn.settings or {}
    if conn.provider == "azure":
        out = help_.azure(cfg.get("scope"), cfg.get("storage_account"), cfg.get("container"))
    elif conn.provider == "gcp":
        projects = sorted(
            p
            for p in db.scalars(
                select(CostLineItem.sub_account_id)
                .where(CostLineItem.source_key == f"gcp:{conn.id}")
                .distinct()
                .limit(20)
            )
            if p
        )
        out = help_.gcp(cfg.get("table"), cfg.get("query_project"), projects or None)
    else:
        raise AppError(
            "Setup help is for Azure and Google Cloud connections.", code="not_supported"
        )
    first, last, rows = db.execute(
        select(
            func.min(CostLineItem.charge_date),
            func.max(CostLineItem.charge_date),
            func.count(),
        ).where(CostLineItem.source_key.like(f"%:{conn.id}"))
    ).one()
    runs = db.scalars(
        select(CostImportRun)
        .where(CostImportRun.source_key.like(f"%:{conn.id}"))
        .order_by(CostImportRun.started_at.desc())
        .limit(6)
    ).all()
    out["last_collection"] = {
        "scope": cfg.get("scope") or cfg.get("table"),
        "checked_at": conn.last_checked_at.isoformat() if conn.last_checked_at else None,
        "error": conn.last_error,
        "data_from": first.isoformat() if first else None,
        "data_to": last.isoformat() if last else None,
        "rows": int(rows or 0),
        "runs": [
            {
                "billing_period": r.billing_period.isoformat(),
                "status": r.status,
                "rows": r.rows,
                "total_cost": float(r.total_cost) if r.total_cost is not None else None,
                "currency": r.currency,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "error": r.error,
            }
            for r in runs
        ],
        "advice": cfg.get("advice"),
    }
    return out


@router.post("/{connection_id}/bedrock-logs/import")
def import_bedrock_logs(
    connection_id: uuid.UUID, auth: Admin, db: SessionDep, request: Request
) -> dict[str, Any]:
    """Read the last three days of Bedrock invocation logs now."""
    from ccradar.clouds.aws.bedrock_logs import import_logs

    conn = _get(db, connection_id)
    if not ((conn.settings or {}).get("bedrock_logs") or {}).get("bucket"):
        raise AppError("Set the Bedrock log bucket first.", code="no_bedrock_logs")
    return import_logs(db, request.app.state.aws, conn.id)


@router.post("/{connection_id}/import", status_code=202)
def collect_now(
    connection_id: uuid.UUID, body: ImportRequest, auth: Admin, db: SessionDep, request: Request
) -> dict[str, str]:
    """Import costs now (Cost Explorer charges about USD 0.01 per request)."""
    conn = _get(db, connection_id)
    if conn.status != "active":
        raise AppError("Finish connecting this account first.", status=409, code="not_active")
    jobs = request.app.state.jobs
    if conn.provider in ("azure", "gcp"):
        jobs.enqueue(
            f"costs.import_{conn.provider}", connection_id=str(conn.id), months=body.months
        )
        return {"status": "queued", "source": conn.provider}
    if conn.provider != "aws":  # Snowflake, Databricks, Datadog, MongoDB Atlas
        jobs.enqueue("costs.import_saas", connection_id=str(conn.id), months=body.months)
        return {"status": "queued", "source": conn.provider}
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
    """Probe every read-only permission CloudCostRadar uses; results are kept on the connection."""
    from ccradar.clouds.aws import permissions

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


@router.post("/{connection_id}/export-check")
def export_check(
    connection_id: uuid.UUID, auth: Admin, db: SessionDep, request: Request
) -> dict[str, Any]:
    """Check now that the billing export can be read (lists it; downloads nothing)."""
    from ccradar.clouds.export_check import check

    conn = _get(db, connection_id)
    return check(conn, aws=request.app.state.aws)
