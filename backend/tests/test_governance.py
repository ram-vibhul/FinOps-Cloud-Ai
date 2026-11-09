from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from costlens.accounts.roles import Role
from costlens.costs import governance
from costlens.costs.ingest import CostLine, replace_billing_period
from costlens.db import Database
from costlens.db.base import utcnow
from costlens.db.tenant import set_tenant
from costlens.inventory.aws import Found
from costlens.inventory.service import upsert
from costlens.k8s.service import ingest_objects
from costlens.models import K8sCluster
from tests.helpers import add_to_org, make_org, sign_in_client

EC2 = "Amazon EC2"


def _seed(db: Database):
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        upsert(
            s,
            org_id,
            "111",
            [
                Found(
                    EC2,
                    "ec2_instance",
                    "i-ok",
                    "eu-west-1",
                    name="web",
                    state="running",
                    tags={"Owner": "ravi", "env": "prod"},
                    attributes={"instance_type": "m7g.large"},
                ),
                Found(
                    EC2,
                    "ec2_instance",
                    "i-bad",
                    "us-west-2",
                    name="old",
                    state="running",
                    tags={"env": "playground"},
                    attributes={"instance_type": "x1e.32xlarge"},
                ),
            ],
        )
        cluster = K8sCluster(
            id=uuid.uuid4(), org_id=org_id, name="prod", token_hash="x" * 64, token_hint="x"
        )
        s.add(cluster)
        s.flush()
        ingest_objects(
            s,
            cluster,
            "Deployment",
            "snap",
            [
                {
                    "uid": "d1",
                    "name": "api",
                    "namespace": "shop",
                    "data": {
                        "containers": [
                            {
                                "name": "app",
                                "resources": {"requests": {"cpu": "100m", "memory": "128Mi"}},
                            },
                            {"name": "sidecar", "resources": {}},
                        ]
                    },
                },
                {
                    "uid": "d2",
                    "name": "ok",
                    "namespace": "shop",
                    "data": {
                        "containers": [
                            {
                                "name": "app",
                                "resources": {"requests": {"cpu": "1", "memory": "1Gi"}},
                            }
                        ]
                    },
                },
            ],
            final=True,
        )
    line = [
        CostLine(
            charge_date=utcnow().date() - timedelta(days=d),
            provider="aws",
            service_name=EC2,
            resource_id="i-bad",
            billed_cost=Decimal(40),
            effective_cost=Decimal(40),
            billing_currency="USD",
        )
        for d in range(1, 31)
    ]
    with db.session() as s:
        for month in sorted({ln.charge_date.replace(day=1) for ln in line}):
            replace_billing_period(
                s,
                org_id=org_id,
                source_kind="t",
                source_key=str(month),
                billing_period=month,
                lines=[ln for ln in line if ln.charge_date.replace(day=1) == month],
            )
    return org_id, owner


def test_policies_and_violations(client: TestClient, db: Database, monkeypatch) -> None:
    sent: list = []
    monkeypatch.setattr(
        governance, "notify", lambda db, event, messages: sent.extend(m.title for m in messages)
    )
    org_id, owner = _seed(db)
    member = add_to_org(db, org_id, "m@acme.com", Role.MEMBER)
    m = sign_in_client(client, db, member, org_id)
    tags = {
        "name": "Owner tag",
        "kind": "required_tags",
        "params": {"tags": ["owner"], "values": {"env": []}},
        "resource_types": ["ec2_instance"],
    }
    assert m.post("/api/v1/governance/policies", json=tags).status_code == 403
    o = sign_in_client(client, db, owner, org_id)
    assert o.post("/api/v1/governance/policies", json={**tags, "params": {}}).status_code == 422
    r = o.post("/api/v1/governance/policies", json=tags)
    assert r.status_code == 201 and r.json()["open"] == 1
    assert r.json()["monthly_cost"] > 1200  # the expensive one shows its cost
    o.post(
        "/api/v1/governance/policies",
        json={
            "name": "EU only",
            "kind": "allowed_regions",
            "params": {"regions": ["eu-west-1"]},
            "severity": "critical",
        },
    )
    o.post(
        "/api/v1/governance/policies",
        json={
            "name": "No x1e",
            "kind": "denied_instance_types",
            "params": {"patterns": ["x1e.*", "p4d.*"]},
        },
    )
    o.post("/api/v1/governance/policies", json={"name": "Requests", "kind": "k8s_requests"})
    rows = {(v["policy"], v["name"]): v for v in o.get("/api/v1/governance/violations").json()}
    assert rows[("Owner tag", "old")]["detail"] == "missing owner"
    assert rows[("EU only", "old")]["severity"] == "critical"
    assert rows[("No x1e", "old")]["detail"] == "x1e.32xlarge matches the denied pattern x1e.*"
    assert (
        rows[("Requests", "prod/shop/api")]["detail"]
        == "containers without CPU and memory requests: sidecar"
    )
    assert ("Requests", "prod/shop/ok") not in rows and ("Owner tag", "web") not in rows
    assert "Owner tag: 1 new violation(s)" in sent
    vid = rows[("No x1e", "old")]["id"]
    m = sign_in_client(client, db, member, org_id)
    assert m.post(
        f"/api/v1/governance/violations/{vid}/exempt", json={"note": "SAP HANA needs it"}
    ).json() == {"status": "exempt"}
    # Fixing the tag resolves its violation on the next run; the exemption stays.
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        upsert(
            s,
            org_id,
            "111",
            [
                Found(
                    EC2,
                    "ec2_instance",
                    "i-bad",
                    "us-west-2",
                    name="old",
                    state="running",
                    tags={"owner": "data"},
                    attributes={"instance_type": "x1e.32xlarge"},
                )
            ],
        )
    o = sign_in_client(client, db, owner, org_id)
    counts = o.post("/api/v1/governance/run").json()
    assert counts["resolved"] == 1
    statuses = {
        (v["policy"], v["status"])
        for v in o.get("/api/v1/governance/violations", params={"status": "all"}).json()
    }
    assert ("Owner tag", "resolved") in statuses and ("No x1e", "exempt") in statuses
