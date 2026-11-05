from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from costlens.accounts.roles import Role
from costlens.clouds.aws import policy
from costlens.clouds.aws.client import AwsAccessError, account_from_arn
from costlens.config import Settings
from costlens.db import Database
from costlens.db.tenant import set_tenant
from costlens.jobs import InProcessRunner
from costlens.main import create_app
from costlens.models import CloudAccount
from tests.conftest import offline_http_client, public_resolver
from tests.fakes.aws import FakeAws
from tests.helpers import add_to_org, make_org, sign_in_client

PRINCIPAL = "arn:aws:iam::999999999999:role/costlens-collector"
ROLE = "arn:aws:iam::123456789012:role/CostLensReadOnly"


@pytest.fixture
def env(settings: Settings, db: Database):
    settings.aws_principal_arn = PRINCIPAL
    aws = FakeAws()
    app = create_app(
        settings,
        db=db,
        runner=InProcessRunner(),
        resolver=public_resolver,
        http_client=offline_http_client(),
        aws=aws,
    )
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    member = add_to_org(db, org_id, "m@acme.com", Role.MEMBER)
    with TestClient(app) as c:
        yield {"c": c, "db": db, "aws": aws, "org": org_id, "owner": owner, "member": member}


def admin(env) -> TestClient:
    return sign_in_client(env["c"], env["db"], env["owner"], env["org"])


# --- policy ---------------------------------------------------------------------------


def test_policy_is_read_only() -> None:
    for action in policy.READ_ACTIONS:
        _service, verb = action.split(":")
        assert not verb.startswith(policy.FORBIDDEN_VERB_PREFIXES), action
    doc = policy.policy_document()
    deny = next(s for s in doc["Statement"] if s["Effect"] == "Deny")
    assert "secretsmanager:GetSecretValue" in deny["Action"]
    assert all(s["Sid"] != "ReadCostAndUsageExport" for s in doc["Statement"])


def test_cur_bucket_access_is_scoped_to_prefix() -> None:
    doc = policy.policy_document("acme-billing", "/cur/costlens/")
    stmt = next(s for s in doc["Statement"] if s["Sid"] == "ReadCostAndUsageExport")
    assert stmt["Resource"] == [
        "arn:aws:s3:::acme-billing",
        "arn:aws:s3:::acme-billing/cur/costlens/*",
    ]


def test_trust_policy_requires_external_id() -> None:
    template = json.loads(
        policy.cloudformation_template(principal_arn=PRINCIPAL, external_id="ext-1")
    )
    trust = template["Resources"]["CostLensRole"]["Properties"]["AssumeRolePolicyDocument"]
    stmt = trust["Statement"][0]
    assert stmt["Principal"] == {"AWS": PRINCIPAL}
    assert stmt["Condition"] == {"StringEquals": {"sts:ExternalId": "ext-1"}}
    tf = policy.terraform_module(principal_arn=PRINCIPAL, external_id="ext-1")
    assert '"sts:ExternalId": "ext-1"' in tf and 'resource "aws_iam_role" "costlens"' in tf


@pytest.mark.parametrize(
    "arn",
    [
        "arn:aws:iam::123:role/x",
        "arn:aws:iam::123456789012:user/x",
        "not-an-arn",
        "arn:aws:iam::123456789012:role/",
    ],
)
def test_role_arn_validation(arn: str) -> None:
    with pytest.raises(AwsAccessError):
        account_from_arn(arn)
    assert account_from_arn("arn:aws-cn:iam::123456789012:role/Path/Name") == "123456789012"


# --- flow ---------------------------------------------------------------------------


def test_connect_flow(env) -> None:
    c = admin(env)
    conn = c.post(
        "/api/v1/connections/aws",
        json={"name": "Production", "cur_bucket": "acme-cur", "cur_prefix": "exports"},
    ).json()
    assert conn["status"] == "pending" and conn["external_id"].startswith("costlens-")
    setup = c.get(f"/api/v1/connections/{conn['id']}/aws-setup").json()
    template = json.loads(setup["cloudformation"])
    trust = template["Resources"]["CostLensRole"]["Properties"]["AssumeRolePolicyDocument"]
    assert (
        trust["Statement"][0]["Condition"]["StringEquals"]["sts:ExternalId"] == conn["external_id"]
    )
    assert "acme-cur" in setup["cloudformation"]

    env["aws"].allow(ROLE, conn["external_id"])
    env["aws"].management_account = "123456789012"
    r = c.post(f"/api/v1/connections/{conn['id']}/verify", json={"role_arn": ROLE})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "active" and body["account_id"] == "123456789012"
    assert env["aws"].calls[0][1][1] == conn["external_id"]
    with env["db"].session() as s:
        set_tenant(s, org_id=env["org"])
        account = s.scalar(select(CloudAccount).where(CloudAccount.external_id == "123456789012"))
        assert account is not None and account.is_billing_account and account.name == "Production"


def test_wrong_external_id_records_error(env) -> None:
    c = admin(env)
    conn = c.post("/api/v1/connections/aws", json={"name": "Prod"}).json()
    env["aws"].allow(ROLE, "someone-elses-external-id")
    r = c.post(f"/api/v1/connections/{conn['id']}/verify", json={"role_arn": ROLE})
    assert r.status_code == 400 and r.json()["code"] == "access_denied"
    listed = c.get("/api/v1/connections").json()
    assert listed[0]["status"] == "error" and "refused" in listed[0]["last_error"]


def test_account_mismatch_and_duplicates(env) -> None:
    c = admin(env)
    first = c.post("/api/v1/connections/aws", json={"name": "A"}).json()
    env["aws"].allow(ROLE, first["external_id"])
    env["aws"].identity_override = "000000000000"
    r = c.post(f"/api/v1/connections/{first['id']}/verify", json={"role_arn": ROLE})
    assert r.json()["code"] == "account_mismatch"
    env["aws"].identity_override = None
    assert (
        c.post(f"/api/v1/connections/{first['id']}/verify", json={"role_arn": ROLE}).status_code
        == 200
    )
    second = c.post("/api/v1/connections/aws", json={"name": "B"}).json()
    env["aws"].allow(ROLE, second["external_id"])
    dup = c.post(f"/api/v1/connections/{second['id']}/verify", json={"role_arn": ROLE})
    assert dup.status_code == 409 and dup.json()["code"] == "already_connected"


def test_permissions_and_isolation(env) -> None:
    c = admin(env)
    conn = c.post("/api/v1/connections/aws", json={"name": "Prod"}).json()
    m = sign_in_client(env["c"], env["db"], env["member"], env["org"])
    assert m.get("/api/v1/connections").status_code == 200  # members can see connections
    assert m.post("/api/v1/connections/aws", json={"name": "x"}).status_code == 403
    assert m.get(f"/api/v1/connections/{conn['id']}/aws-setup").status_code == 403
    other, boss = make_org(env["db"], "Other", "b@other.com")
    o = sign_in_client(env["c"], env["db"], boss, other)
    assert o.get("/api/v1/connections").json() == []
    assert (
        o.post(f"/api/v1/connections/{conn['id']}/verify", json={"role_arn": ROLE}).status_code
        == 404
    )


def test_update_delete_and_audit(env) -> None:
    c = admin(env)
    conn = c.post("/api/v1/connections/aws", json={"name": "Prod"}).json()
    assert (
        c.patch(f"/api/v1/connections/{conn['id']}", json={"cur_bucket": "Bad_Bucket"}).status_code
        == 422
    )
    assert (
        c.patch(f"/api/v1/connections/{conn['id']}", json={"name": "Production"}).json()["name"]
        == "Production"
    )
    assert c.delete(f"/api/v1/connections/{conn['id']}").status_code == 204
    actions = [
        e["action"]
        for e in c.get("/api/v1/org/audit-events", params={"action": "cloud_connection"}).json()[
            "items"
        ]
    ]
    assert actions == [
        "cloud_connection.deleted",
        "cloud_connection.updated",
        "cloud_connection.created",
    ]


def test_not_configured(env) -> None:
    env["c"].app.state.settings.aws_principal_arn = None  # type: ignore[attr-defined]
    r = admin(env).post("/api/v1/connections/aws", json={"name": "x"})
    assert r.status_code == 503 and r.json()["code"] == "aws_not_configured"


def test_boto3_gateway_maps_errors_and_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real gateway, with boto3's own Stubber instead of AWS."""
    from datetime import UTC, datetime, timedelta

    import boto3
    from botocore.stub import Stubber

    from costlens.clouds.aws.client import Boto3Gateway

    sts = boto3.client(
        "sts", region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="y"
    )
    stub = Stubber(sts)
    stub.add_client_error("assume_role", service_error_code="AccessDenied", http_status_code=403)
    expires = datetime.now(UTC) + timedelta(hours=1)
    stub.add_response(
        "assume_role",
        {
            "Credentials": {
                "AccessKeyId": "AKIAEXAMPLEEXAMPLE12",
                "SecretAccessKey": "s" * 40,
                "SessionToken": "t" * 20,
                "Expiration": expires,
            }
        },
    )
    stub.activate()
    monkeypatch.setattr(boto3, "client", lambda *a, **k: sts)
    gateway = Boto3Gateway()
    with pytest.raises(AwsAccessError) as exc:
        gateway.assume(ROLE, "ext", "costlens-test")
    assert exc.value.code == "access_denied"
    first = gateway.assume(ROLE, "ext", "costlens-test")
    assert gateway.assume(ROLE, "ext", "costlens-test") is first  # cached until near expiry
    stub.assert_no_pending_responses()
