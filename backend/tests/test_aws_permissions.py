from __future__ import annotations

import json
from typing import Any

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

from costlens.clouds.aws import permissions
from costlens.config import Settings
from costlens.db import Database
from costlens.db.tenant import set_tenant
from costlens.jobs import InProcessRunner
from costlens.main import create_app
from costlens.models import CloudConnection
from tests.conftest import offline_http_client, public_resolver
from tests.fakes.aws import FakeAws
from tests.helpers import make_org, sign_in_client

ROLE = "arn:aws:iam::123456789012:role/CostLensReadOnly"


class ProbeClient:
    """Answers any call; methods listed in ``codes`` raise that AWS error code."""

    def __init__(self, codes: dict[str, str]) -> None:
        self.codes = codes
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __getattr__(self, name: str):
        def call(**kwargs: Any) -> dict[str, Any]:
            self.calls.append((name, kwargs))
            if name in self.codes:
                raise ClientError({"Error": {"Code": self.codes[name], "Message": "x"}}, name)
            return {}

        return call


@pytest.fixture
def env(settings: Settings, db: Database):
    codes = {
        "describe_volumes": "UnauthorizedOperation",
        "lookup_events": "AccessDeniedException",
        "describe_trusted_advisor_checks": "SubscriptionRequiredException",
        "get_enrollment_status": "ThrottlingException",
        "list_objects_v2": "AccessDenied",
    }
    client = ProbeClient(codes)

    class AllServices(dict):
        def __missing__(self, key: str) -> ProbeClient:
            return client

    aws = FakeAws(clients=AllServices())
    aws.allow(ROLE, "ext")
    settings.aws_principal_arn = "arn:aws:iam::999999999999:role/c"
    app = create_app(
        settings,
        db=db,
        runner=InProcessRunner(),
        resolver=public_resolver,
        http_client=offline_http_client(),
        aws=aws,
    )
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        conn = CloudConnection(
            org_id=org_id,
            provider="aws",
            name="Prod",
            status="active",
            external_id="ext",
            role_arn=ROLE,
            account_id="123456789012",
            cur_bucket="acme-cur",
            cur_prefix="/exports/",
        )
        s.add(conn)
        s.flush()
        conn_id = conn.id
    with TestClient(app) as c:
        yield {
            "c": sign_in_client(c, db, owner, org_id),
            "client": client,
            "conn": conn_id,
            "aws": aws,
        }


def test_permission_report(env) -> None:
    r = env["c"].post(f"/api/v1/connections/{env['conn']}/permissions-check").json()
    by = {x["permission"]: x for x in r["results"]}
    assert by["ec2:DescribeInstances"]["status"] == "ok"
    assert by["ec2:DescribeVolumes"]["status"] == "missing"
    assert by["ec2:DescribeVolumes"]["message"] == "Unattached volumes will not work."
    assert by["cloudtrail:LookupEvents"]["status"] == "missing"
    assert by["support:DescribeTrustedAdvisorChecks"]["status"] == "unavailable"
    assert by["compute-optimizer:GetEnrollmentStatus"]["status"] == "error"
    assert by["s3:ListBucket (billing export)"]["status"] == "missing"
    assert set(r["missing"]) == {
        "ec2:DescribeVolumes",
        "cloudtrail:LookupEvents",
        "s3:ListBucket (billing export)",
    }
    snippet = json.loads(r["policy_snippet"])
    assert snippet["Action"] == [
        "cloudtrail:LookupEvents",
        "ec2:DescribeVolumes",
        "s3:ListBucket",
        "s3:GetObject",
    ]
    assert ("list_objects_v2", {"MaxKeys": 1, "Bucket": "acme-cur", "Prefix": "exports"}) in env[
        "client"
    ].calls
    # Stored for later viewing.
    assert (
        env["c"].get(f"/api/v1/connections/{env['conn']}/permissions-check").json()["missing"]
        == r["missing"]
    )


def test_no_cost_explorer_calls_and_read_only_methods() -> None:
    names = [p.method for p in permissions.PROBES]
    assert not any(n.startswith(("get_cost", "get_dimension")) for n in names)
    assert all(n.startswith(("describe_", "list_", "get_", "lookup_")) for n in names)


def test_revoked_role(env) -> None:
    env["aws"].trusted.clear()
    r = env["c"].post(f"/api/v1/connections/{env['conn']}/permissions-check").json()
    assert r["role"] == "error" and r["results"] == []
