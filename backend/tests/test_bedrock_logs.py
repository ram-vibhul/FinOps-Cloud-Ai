from __future__ import annotations

import gzip
import json
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from costlens.clouds.aws import bedrock_logs, policy
from costlens.db import Database
from costlens.db.tenant import set_tenant
from costlens.models import AiUsageEvent, CloudConnection
from tests.fakes.aws import FakeAws, FakeS3
from tests.helpers import make_org, sign_in_client

ROLE = "arn:aws:iam::111111111111:role/CostLensReadOnly"
BUCKET = "acme-bedrock-logs"
SECRET_PROMPT = "Write a refund email to Meera Iyer about order 88123"
SECRET_ANSWER = "Dear Meera, your refund of 4,250 rupees"


def record(
    day: int,
    hour: int,
    *,
    model: str = "us.anthropic.claude-sonnet-4-6-v1:0",
    meta=None,
    role="checkout-api",
    err=None,
):
    return {
        "schemaType": "ModelInvocationLog",
        "timestamp": f"2026-11-{day:02d}T{hour:02d}:15:00Z",
        "accountId": "111111111111",
        "region": "us-east-1",
        "requestId": f"r-{day}-{hour}",
        "operation": "Converse",
        "modelId": model,
        "identity": {"arn": f"arn:aws:sts::111111111111:assumed-role/{role}/session-1"},
        "requestMetadata": meta or {},
        "input": {
            "inputContentType": "application/json",
            "inputBodyJson": {"messages": [{"role": "user", "content": [{"text": SECRET_PROMPT}]}]},
            "inputTokenCount": 1_000_000,
            "cacheReadInputTokenCount": 0,
        },
        "output": {
            "outputContentType": "application/json",
            "outputBodyJson": {"output": {"message": {"content": [{"text": SECRET_ANSWER}]}}},
            "outputTokenCount": 100_000,
        },
        **({"errorCode": err} if err else {}),
    }


def key(day: int, name: str) -> str:
    return f"logs/AWSLogs/111111111111/BedrockModelInvocationLogs/us-east-1/2026/11/{day:02d}/10/{name}.json.gz"


def blob(*records) -> bytes:
    return gzip.compress("\n".join(json.dumps(r) for r in records).encode())


@pytest.fixture
def env(db: Database):
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        conn = CloudConnection(
            org_id=org_id,
            provider="aws",
            name="Payer",
            status="active",
            external_id="ext",
            role_arn=ROLE,
            account_id="111111111111",
            settings={
                "bedrock_logs": {
                    "bucket": BUCKET,
                    "prefix": "logs",
                    "rules": [{"match": "batch-worker", "app": "reports", "team": "finance"}],
                }
            },
        )
        s.add(conn)
        s.flush()
        conn_id = conn.id
    s3 = FakeS3()
    aws = FakeAws(clients={"s3": s3})
    aws.allow(ROLE, "ext")
    return {"org": org_id, "owner": owner, "conn": conn_id, "s3": s3, "aws": aws}


def events(db: Database, org_id) -> list[AiUsageEvent]:
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        return list(s.scalars(select(AiUsageEvent).order_by(AiUsageEvent.at)))


def test_counts_and_attribution_only_never_text(db: Database, env) -> None:
    env["s3"].put(BUCKET, key(8, "a"), blob(
        record(8, 1, meta={"App": "Support Bot", "team": "cx", "feature": "refunds"}),
        record(8, 2, role="batch-worker"),
        record(8, 3, role="checkout-api", err="ThrottlingException"),
    ))  # fmt: skip
    env["s3"].put(BUCKET, key(1, "old"), blob(record(1, 1)))  # outside the window: not read
    with db.session() as s:
        out = bedrock_logs.import_logs(s, env["aws"], env["conn"], days=3, today=date(2026, 11, 9))
    assert out == {"files": 1, "events": 3, "unpriced": 0}
    assert env["s3"].downloads == [key(8, "a")]
    rows = events(db, env["org"])
    stored = json.dumps([{k: str(v) for k, v in vars(r).items()} for r in rows])
    for word in ("Meera", "refund email", "88123", "4,250", "Dear"):
        assert word not in stored
    a, b, c = rows
    assert (a.app, a.team, a.feature) == ("Support-Bot", "cx", "refunds")  # request metadata first
    assert (b.app, b.team) == ("reports", "finance")  # then the role rule
    assert (c.app, c.team, c.status) == ("checkout-api", None, "error")  # then the role name
    assert a.provider == "bedrock" and a.model == "us.anthropic.claude-sonnet-4-6-v1:0"
    # Sonnet 4.x list price: 3 in, 15 out per million tokens.
    assert a.cost_usd == pytest.approx(3.0 + 1.5) and a.source == f"bedrock_logs:{env['conn']}"


def test_rerun_replaces_the_same_days(db: Database, env) -> None:
    env["s3"].put(BUCKET, key(8, "a"), blob(record(8, 1)))
    for _ in range(2):
        with db.session() as s:
            bedrock_logs.import_logs(s, env["aws"], env["conn"], days=3, today=date(2026, 11, 9))
    assert len(events(db, env["org"])) == 1


def test_access_errors_are_recorded(db: Database, env) -> None:
    env["aws"].trusted.clear()
    with db.session() as s:
        assert bedrock_logs.import_logs(s, env["aws"], env["conn"], today=date(2026, 11, 9)) == {
            "error": "AwsAccessError"
        }
    with db.session() as s:
        set_tenant(s, bypass=True)
        conn = s.get(CloudConnection, env["conn"])
        assert conn is not None and "AwsAccessError" in conn.settings["bedrock_logs"]["error"]


def test_policy_and_settings_api(client: TestClient, db: Database, env) -> None:
    doc = policy.policy_document(None, None, BUCKET, "logs")
    stmt = next(x for x in doc["Statement"] if x["Sid"] == "ReadBedrockInvocationLogs")
    assert stmt["Action"] == ["s3:GetObject", "s3:ListBucket"]
    assert stmt["Resource"] == [f"arn:aws:s3:::{BUCKET}", f"arn:aws:s3:::{BUCKET}/logs/AWSLogs/*"]
    c = sign_in_client(client, db, env["owner"], env["org"])
    r = c.patch(
        f"/api/v1/connections/{env['conn']}",
        json={
            "bedrock_log_bucket": "other-bucket",
            "bedrock_log_prefix": "/x/",
            "bedrock_rules": [{"match": "etl", "app": "etl", "team": "data"}, {"app": "no-match"}],
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["bedrock_logs"] == {"bucket": "other-bucket", "prefix": "x"}
    with db.session() as s:
        set_tenant(s, bypass=True)
        conn = s.get(CloudConnection, env["conn"])
        assert conn is not None and conn.settings["bedrock_logs"]["rules"] == [
            {"match": "etl", "app": "etl", "team": "data"}
        ]
    assert (
        c.patch(
            f"/api/v1/connections/{env['conn']}", json={"bedrock_log_bucket": "Bad_Bucket"}
        ).status_code
        == 422
    )
    client.app.state.aws = env["aws"]  # type: ignore[attr-defined]
    out = c.post(f"/api/v1/connections/{env['conn']}/bedrock-logs/import").json()
    assert out["files"] == 0
