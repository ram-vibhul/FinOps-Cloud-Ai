from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from costlens.clouds import setup_help
from costlens.costs.ingest import CostLine, replace_billing_period
from costlens.db import Database
from costlens.db.tenant import set_tenant
from costlens.models import CloudConnection
from tests.helpers import make_org, sign_in_client

SUB = "33333333-3333-3333-3333-333333333333"


def test_azure_commands_use_the_scope_and_container() -> None:
    out = setup_help.azure(f"/subscriptions/{SUB}", "costexports", "billing", "rg-finops")
    roles = [r["role"] for r in out["roles"]]
    assert roles == ["Cost Management Reader", "Reader", "Storage Blob Data Reader"]
    text = "\n".join(out["commands"])
    assert f"--scopes '/subscriptions/{SUB}'" in text
    assert "storageAccounts/costexports/blobServices/default/containers/billing" in text
    assert "rg-finops" in text


def test_gcp_commands_cover_dataset_job_user_and_projects() -> None:
    out = setup_help.gcp("acme-billing.billing.export_v1", None, ["web-prod"])
    text = "\n".join(out["commands"])
    assert "bq add-iam-policy-binding" in text and "'acme-billing:billing'" in text
    assert "roles/bigquery.jobUser" in text and "'web-prod'" in text
    assert "costlens-reader@acme-billing.iam.gserviceaccount.com" in text
    assert {r["role"] for r in out["roles"]} >= {
        "roles/bigquery.dataViewer",
        "roles/recommender.computeViewer",
    }


def test_unsafe_values_are_dropped() -> None:
    assert setup_help.safe("/subscriptions/x; rm -rf /") is None
    assert setup_help.safe("acme-billing.billing.t") == "acme-billing.billing.t"


def test_api_before_and_after_connecting(client: TestClient, db: Database) -> None:
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    c = sign_in_client(client, db, owner, org_id)
    pre = c.get(
        "/api/v1/connections/setup-help/azure", params={"scope": f"/subscriptions/{SUB}"}
    ).json()
    assert pre["provider"] == "azure" and f"/subscriptions/{SUB}" in "\n".join(pre["commands"])
    evil = c.get("/api/v1/connections/setup-help/gcp", params={"table": "x`; drop"}).json()
    assert "drop" not in "\n".join(evil["commands"])
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        conn = CloudConnection(
            org_id=org_id,
            provider="gcp",
            name="GCP",
            status="active",
            external_id="gcp-x",
            settings={"table": "acme-billing.billing.export_v1", "query_project": "acme-billing"},
            last_error="403: PERMISSION_DENIED",
        )
        s.add(conn)
        s.flush()
        cid = conn.id
        replace_billing_period(
            s,
            org_id=org_id,
            source_kind="gcp_billing_export",
            source_key=f"gcp:{cid}",
            billing_period=date(2026, 10, 1),
            lines=[
                CostLine(
                    charge_date=date(2026, 10, d),
                    provider="gcp",
                    sub_account_id="web-prod",
                    service_name="Compute Engine",
                    billed_cost=Decimal(5),
                    effective_cost=Decimal(5),
                    billing_currency="USD",
                )
                for d in (3, 9)
            ],
        )
    out = c.get(f"/api/v1/connections/{cid}/setup-help").json()
    seen = out["last_collection"]
    assert (
        seen["data_from"] == "2026-10-03" and seen["data_to"] == "2026-10-09" and seen["rows"] == 2
    )
    assert (
        seen["error"] == "403: PERMISSION_DENIED"
        and seen["runs"][0]["billing_period"] == "2026-10-01"
    )
    assert "'web-prod'" in "\n".join(out["commands"])  # projects taken from the bill
