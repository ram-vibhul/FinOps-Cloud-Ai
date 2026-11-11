from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ccradar.accounts.trial import start_trial, trial_status
from ccradar.clouds import export_check
from ccradar.config import DeploymentMode, Settings
from ccradar.db import Database
from ccradar.db.tenant import set_tenant
from ccradar.jobs import InProcessRunner
from ccradar.main import create_app
from ccradar.models import CloudConnection, Finding, InboxItem, Organization
from tests.conftest import offline_http_client, public_resolver
from tests.fakes.aws import FakeAws, FakeS3
from tests.helpers import make_org, sign_in_client
from tests.test_azure import BLOB_LIST, CLIENT, SUB, TENANT, FakeAzure, make_client

ROLE = "arn:aws:iam::123456789012:role/CloudCostRadarReadOnly"
NOW = datetime(2026, 11, 9, 12, tzinfo=UTC)


@pytest.fixture
def env(settings: Settings, db: Database):
    settings.aws_principal_arn = "arn:aws:iam::999999999999:role/ccradar-collector"
    aws, runner = FakeAws(), InProcessRunner()
    app = create_app(
        settings,
        db=db,
        runner=runner,
        resolver=public_resolver,
        http_client=offline_http_client(),
        aws=aws,
    )
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    with TestClient(app) as c:
        yield {"c": c, "db": db, "aws": aws, "org": org_id, "owner": owner, "runner": runner}


def connect(env, name: str = "Prod", account: str = "123456789012", **body: Any) -> dict[str, Any]:
    c = sign_in_client(env["c"], env["db"], env["owner"], env["org"])
    conn = c.post("/api/v1/connections/aws", json={"name": name, **body}).json()
    role = ROLE.replace("123456789012", account)
    env["aws"].allow(role, conn["external_id"])
    r = c.post(f"/api/v1/connections/{conn['id']}/verify", json={"role_arn": role})
    assert r.status_code == 200, r.text
    return r.json()


def add_finding(db: Database, org_id, title: str, saving: float, key: str, n: int = 0) -> None:
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        s.add(
            Finding(
                org_id=org_id,
                detector="test.manual",
                category="waste",
                dedup_key=f"t:{key}:{n}",
                overlap_key=key,
                title=title,
                savings_monthly=saving,
                currency="USD",
                risk="low",
                effort="low",
                first_seen_at=NOW,
                last_seen_at=NOW,
            )
        )


def test_first_connection_runs_everything_at_once(env) -> None:
    c = sign_in_client(env["c"], env["db"], env["owner"], env["org"])
    assert c.get("/api/v1/org/setup/first-savings").json()["stage"] == "connect"
    add_finding(env["db"], env["org"], "Delete 3 unattached volumes", 120, "vol")
    add_finding(env["db"], env["org"], "Smaller database", 900, "db")
    add_finding(env["db"], env["org"], "Same database, other idea", 300, "db", 1)  # overlaps
    connect(env)
    assert "onboarding.first_run" in env["runner"].completed
    c = sign_in_client(env["c"], env["db"], env["owner"], env["org"])
    out = c.get("/api/v1/org/setup/first-savings").json()
    assert out["stage"] == "ready" and out["count"] == 2 and out["savings_monthly"] == 1020
    assert [t["title"] for t in out["top"]] == ["Smaller database", "Delete 3 unattached volumes"]
    assert out["finished_at"]
    with env["db"].session() as s:
        set_tenant(s, bypass=True)
        titles = [
            i.title for i in s.scalars(select(InboxItem).where(InboxItem.event == "first_savings"))
        ]
    assert titles == ["First savings ready: about 1,020 USD a month"]
    # A second connection imports as usual; the first run happens once.
    runner = env["runner"]
    runner.completed.clear()
    runner.failed.clear()
    connect(env, "Other", "210987654321")
    ran = runner.completed + [name for name, _ in runner.failed]  # the fake has no Cost Explorer
    assert ran == ["costs.import_cost_explorer"]


def test_stages_without_savings(env) -> None:
    connect(env)  # the fake has no Cost Explorer: the import fails, the run still finishes
    c = sign_in_client(env["c"], env["db"], env["owner"], env["org"])
    out = c.get("/api/v1/org/setup/first-savings").json()
    assert out["stage"] == "none_found" and out["top"] == []
    with env["db"].session() as s:
        set_tenant(s, bypass=True)
        org = s.get(Organization, env["org"])
        assert org is not None
        org.settings = {**org.settings, "first_run": {"status": "running"}}
    assert c.get("/api/v1/org/setup/first-savings").json()["stage"] == "importing"


class DeniedS3:
    def list_objects_v2(self, **_: Any) -> dict[str, Any]:
        exc = Exception("denied")
        exc.response = {"Error": {"Code": "AccessDenied"}}  # type: ignore[attr-defined]
        raise exc


def test_export_check_aws(env) -> None:
    conn = connect(env, cur_bucket="acme-cur", cur_prefix="exports")
    c = sign_in_client(env["c"], env["db"], env["owner"], env["org"])
    s3 = FakeS3()
    env["aws"].clients["s3"] = s3
    path = f"/api/v1/connections/{conn['id']}/export-check"
    empty = c.post(path).json()
    assert (
        empty["ok"] and empty["code"] == "empty" and empty["location"] == "s3://acme-cur/exports/"
    )
    for month in ("2026-10", "2026-11"):
        s3.put("acme-cur", f"exports/ccradar/data/BILLING_PERIOD={month}/part-0.parquet", b"x")
    s3.put("acme-cur", "exports/ccradar/metadata/manifest.json", b"{}")
    ready = c.post(path).json()
    assert ready["code"] == "ready" and ready["files"] == 2
    assert ready["months"] == ["2026-10", "2026-11"] and "2 files" in ready["message"]
    env["aws"].clients["s3"] = DeniedS3()
    denied = c.post(path).json()
    assert not denied["ok"] and denied["code"] == "access_denied"
    assert "s3:ListBucket" in denied["message"]
    listed = c.get("/api/v1/connections").json()
    assert listed[0]["export_check"]["code"] == "access_denied"  # kept for the page
    # Without an export, the check says so.
    plain = connect(env, "No export", "210987654321")
    assert c.post(f"/api/v1/connections/{plain['id']}/export-check").json()["code"] == (
        "not_configured"
    )


def test_export_check_azure() -> None:
    conn = CloudConnection(
        provider="azure",
        name="az",
        status="active",
        external_id="az-1",
        settings={
            "tenant_id": TENANT,
            "client_id": CLIENT,
            "scope": f"/subscriptions/{SUB}",
            "storage_account": "acmebilling",
            "container": "exports",
            "prefix": "exports/focus",
        },
    )
    out = export_check.check(conn, aws=FakeAws(), azure_client=make_client(FakeAzure()))
    assert out["code"] == "ready" and out["files"] == 2 and out["months"] == ["2026-10"]
    assert conn.settings["export_check"]["code"] == "ready"
    assert "part_0" in BLOB_LIST

    def forbidden(request: httpx.Request) -> httpx.Response:
        if request.url.host == "login.microsoftonline.com":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return httpx.Response(403, text="AuthorizationPermissionMismatch")

    from ccradar.clouds.azure.client import AzureClient

    client = AzureClient(
        httpx.Client(transport=httpx.MockTransport(forbidden)), TENANT, CLIENT, "s"
    )
    client._fast = True
    out = export_check.check(conn, aws=FakeAws(), azure_client=client)
    assert not out["ok"] and "Storage Blob Data Reader" in out["message"]


def test_trial_countdown(settings: Settings, db: Database) -> None:
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        org = s.get(Organization, org_id)
        assert org is not None
        start_trial(s, org, settings, now=NOW)  # local mode: no trial
        assert trial_status(org, now=NOW)["status"] == "none"
        settings.deployment_mode = DeploymentMode.SAAS
        settings.trial_days = 14
        start_trial(s, org, settings, now=NOW)
        assert trial_status(org, now=NOW) == {
            "status": "active",
            "ends_at": (NOW + timedelta(days=14)).isoformat(),
            "days_left": 14,
        }
        assert trial_status(org, now=NOW + timedelta(days=11, hours=1))["status"] == "ending"
        assert trial_status(org, now=NOW + timedelta(days=13, hours=23))["days_left"] == 1
        assert trial_status(org, now=NOW + timedelta(days=15))["status"] == "ended"
        demo = Organization(name="Demo", slug="demo-x", settings={}, is_demo=True)
        s.add(demo)
        s.flush()
        start_trial(s, demo, settings, now=NOW)
        assert trial_status(demo)["status"] == "none"


def test_trial_api(client: TestClient, db: Database) -> None:
    client.app.state.settings.deployment_mode = DeploymentMode.SAAS  # type: ignore[attr-defined]
    org_id, owner = make_org(db, "Seed", "s@acme.com")
    c = sign_in_client(client, db, owner, org_id)
    assert c.get("/api/v1/org/trial").json()["status"] == "none"  # made outside the API
    c.post("/api/v1/organizations", json={"name": "Fresh"})
    out = c.get("/api/v1/org/trial").json()
    assert out["status"] == "active" and out["days_left"] == 14
