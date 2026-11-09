"""Collect one commitments snapshot for a connection (used by the job and the API)."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from costlens.clouds.aws import commitments
from costlens.clouds.aws.client import AwsAccessError, AwsGateway
from costlens.db.base import utcnow
from costlens.db.tenant import set_tenant
from costlens.models import CloudConnection, CommitmentSnapshot


def snapshot(
    db: Session, gateway: AwsGateway, connection_id: uuid.UUID
) -> CommitmentSnapshot | None:
    set_tenant(db, bypass=True)
    conn = db.get(CloudConnection, connection_id)
    if conn is None or conn.status != "active" or not conn.role_arn:
        return None
    org_id = conn.org_id
    try:
        session = gateway.assume(conn.role_arn, conn.external_id, f"costlens-{org_id}")
    except AwsAccessError:
        return None
    ce = gateway.client(session, "ce", region="us-east-1")
    data = commitments.collect(ce, utcnow().date())
    set_tenant(db, org_id=org_id)
    snap = CommitmentSnapshot(org_id=org_id, connection_id=conn.id, taken_at=utcnow(), data=data)
    db.add(snap)
    db.flush()
    return snap
