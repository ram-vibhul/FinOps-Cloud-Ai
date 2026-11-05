"""Create and verify AWS connections."""

from __future__ import annotations

import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from costlens.clouds.aws.client import AwsAccessError, AwsGateway, account_from_arn
from costlens.core.errors import AppError, ConflictError
from costlens.db.base import utcnow
from costlens.models import CloudAccount, CloudConnection


def new_external_id() -> str:
    return "costlens-" + secrets.token_urlsafe(24)


def create_aws_connection(db: Session, *, org_id: uuid.UUID, name: str) -> CloudConnection:
    conn = CloudConnection(
        org_id=org_id,
        provider="aws",
        name=name.strip() or "AWS",
        status="pending",
        external_id=new_external_id(),
    )
    db.add(conn)
    db.flush()
    return conn


def verify_aws_connection(
    db: Session, gateway: AwsGateway, conn: CloudConnection, role_arn: str
) -> CloudConnection:
    """Assume the role with the connection's external ID and record the account."""
    role_arn = role_arn.strip()
    account_id = account_from_arn(role_arn)
    duplicate = db.scalar(
        select(CloudConnection).where(
            CloudConnection.account_id == account_id,
            CloudConnection.id != conn.id,
            CloudConnection.status == "active",
        )
    )
    if duplicate is not None:
        raise ConflictError(
            f"AWS account {account_id} is already connected as '{duplicate.name}'.",
            code="already_connected",
        )
    conn.role_arn = role_arn
    conn.last_checked_at = utcnow()
    try:
        session = gateway.assume(role_arn, conn.external_id, f"costlens-{conn.org_id}")
        identity = gateway.identity(session)
        if identity.account_id != account_id:
            raise AwsAccessError("account_mismatch", "The role belongs to a different account.")
        master = gateway.organization_master(session)
    except AwsAccessError as exc:
        conn.status = "error"
        conn.last_error = str(exc)
        db.flush()
        raise AppError(str(exc), code=exc.code) from exc
    conn.status = "active"
    conn.last_error = None
    conn.account_id = account_id
    account = db.scalar(
        select(CloudAccount).where(
            CloudAccount.provider == "aws", CloudAccount.external_id == account_id
        )
    )
    if account is None:
        account = CloudAccount(
            org_id=conn.org_id, provider="aws", external_id=account_id, name=conn.name
        )
        db.add(account)
    account.is_billing_account = master == account_id
    db.flush()
    conn.cloud_account_id = account.id
    db.flush()
    return conn
