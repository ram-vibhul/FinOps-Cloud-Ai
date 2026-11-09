from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from costlens import estimate
from costlens.cli import main
from costlens.costs.ingest import CostLine, replace_billing_period
from costlens.db import Database
from tests.helpers import make_org, sign_in_client


def rc(address: str, rtype: str, actions: list[str], before=None, after=None) -> dict:
    return {
        "address": address,
        "mode": "managed",
        "type": rtype,
        "change": {"actions": actions, "before": before, "after": after},
    }


PLAN = {
    "format_version": "1.2",
    "resource_changes": [
        rc(
            "aws_instance.web",
            "aws_instance",
            ["update"],
            {"instance_type": "m5.large"},
            {"instance_type": "m5.2xlarge"},
        ),
        rc("aws_ebs_volume.data", "aws_ebs_volume", ["create"], None, {"size": 100, "type": "gp3"}),
        rc(
            "aws_db_instance.main",
            "aws_db_instance",
            ["create"],
            None,
            {
                "instance_class": "db.m5.large",
                "allocated_storage": 100,
                "storage_type": "gp3",
                "multi_az": True,
            },
        ),
        rc("aws_nat_gateway.a", "aws_nat_gateway", ["delete"], {"subnet_id": "s-1"}, None),
        rc("aws_s3_bucket.logs", "aws_s3_bucket", ["create"], None, {"bucket": "logs"}),
        rc("aws_iam_role.r", "aws_iam_role", ["no-op"], {}, {}),
        {
            "address": "data.aws_ami.x",
            "mode": "data",
            "type": "aws_ami",
            "change": {"actions": ["read"]},
        },
    ],
}


def test_terraform_plan_lines_and_total() -> None:
    e = estimate.terraform(PLAN)
    by = {x.address: x for x in e.lines}
    assert set(by) == {
        "aws_instance.web",
        "aws_ebs_volume.data",
        "aws_db_instance.main",
        "aws_nat_gateway.a",
        "aws_s3_bucket.logs",
    }
    web = by["aws_instance.web"]
    # m5.large 0.096/h -> m5.2xlarge 0.384/h, plus the default 8 GB gp3 root disk on both.
    assert web.before == pytest.approx(0.096 * 730 + 0.64) and web.after == pytest.approx(
        0.384 * 730 + 0.64
    )
    assert web.note == "m5.large → m5.2xlarge" and web.source == "list price"
    assert by["aws_ebs_volume.data"].after == 8.0
    db = by["aws_db_instance.main"]
    assert db.after == pytest.approx((0.1709 * 730 + 100 * 0.115) * 2, abs=0.01)
    assert db.note == "db.m5.large multi-AZ"
    assert by["aws_nat_gateway.a"].change == pytest.approx(-32.85)
    assert by["aws_s3_bucket.logs"].source == "not priced"
    d = e.to_dict()
    assert d["not_priced"] == ["aws_s3_bucket.logs"]
    assert d["monthly_change"] == pytest.approx(
        sum(x.change for x in e.lines if x.source != "not priced")
    )
    md = estimate.markdown(e)
    assert md.startswith("### CostLens estimate: +")
    assert "| `aws_s3_bucket.logs` | create | | | | not priced |" in md
    assert "1 resource(s) could not be priced" in md


def test_bill_prices_win_over_list_prices() -> None:
    e = estimate.terraform(PLAN, {"m5.2xlarge": 0.25, "m5.large": 0.07}, "EUR")
    web = next(x for x in e.lines if x.address == "aws_instance.web")
    assert web.source == "bill" and web.after == pytest.approx(0.25 * 730 + 0.64)
    assert e.currency == "EUR"


def test_kubernetes_manifests_compare_requests() -> None:
    before = """
apiVersion: apps/v1
kind: Deployment
metadata: {name: api, namespace: shop}
spec:
  replicas: 2
  template:
    spec:
      containers:
        - name: app
          resources: {requests: {cpu: 500m, memory: 1Gi}}
---
apiVersion: v1
kind: Service
metadata: {name: api}
"""
    after = (
        before.replace("replicas: 2", "replicas: 6")
        + """
---
apiVersion: apps/v1
kind: DaemonSet
metadata: {name: agent, namespace: ops}
spec:
  template:
    spec:
      containers:
        - name: a
          resources: {requests: {cpu: 100m, memory: 128Mi}}
"""
    )
    e = estimate.kubernetes(after, before, daemon_nodes=4)
    by = {x.address: x for x in e.lines}
    per_pod = 730 * (0.5 * estimate.K8S_CPU_HOUR + 1 * estimate.K8S_GIB_HOUR)
    api = by["Deployment/shop/api"]
    assert api.action == "update" and api.change == pytest.approx(4 * per_pod, abs=0.02)
    assert api.note == "2 x (0.5 CPU, 1.00 GiB) → 6 x (0.5 CPU, 1.00 GiB)"
    agent = by["DaemonSet/ops/agent"]
    assert agent.action == "create" and agent.note.startswith("4 x (0.1 CPU")
    unchanged = estimate.kubernetes(before, before)
    assert unchanged.lines == [] and unchanged.change == 0
    with pytest.raises(estimate.ManifestError):
        estimate.kubernetes("kind: [unclosed")


def test_api_uses_bill_prices(client: TestClient, db: Database) -> None:
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    lines = [
        CostLine(
            charge_date=date(2026, 10, d),
            provider="aws",
            service_name="Amazon EC2",
            usage_type="EUW1-BoxUsage:m5.2xlarge",
            consumed_quantity=Decimal("24"),
            billed_cost=Decimal("6.00"),  # 0.25 an hour after discounts
            effective_cost=Decimal("6.00"),
            billing_currency="USD",
        )
        for d in range(1, 11)
    ]
    with db.session() as s:
        replace_billing_period(
            s,
            org_id=org_id,
            source_kind="t",
            source_key="t",
            billing_period=date(2026, 10, 1),
            lines=lines,
        )
    c = sign_in_client(client, db, owner, org_id)
    p = c.get("/api/v1/estimates/prices").json()
    assert p["prices"] == {"m5.2xlarge": 0.25} and p["currency"] == "USD"
    r = c.post(
        "/api/v1/estimates/terraform",
        content=json.dumps(PLAN),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 200, r.text
    web = next(x for x in r.json()["lines"] if x["address"] == "aws_instance.web")
    assert web["source"] == "bill" and web["after"] == pytest.approx(0.25 * 730 + 0.64)
    assert r.json()["markdown"].startswith("### CostLens estimate")
    bad = c.post(
        "/api/v1/estimates/terraform", content=b"{}", headers={"Content-Type": "application/json"}
    )
    assert bad.json()["code"] == "bad_plan"
    k = c.post("/api/v1/estimates/kubernetes", json={"after": "kind: [oops"})
    assert k.json()["code"] == "bad_manifest"


def test_cli_markdown_json_and_fail_above(tmp_path, capsys) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(PLAN))
    assert main(["estimate", "terraform", str(plan)]) == 0
    assert capsys.readouterr().out.startswith("### CostLens estimate: +")
    prices = tmp_path / "prices.json"
    prices.write_text(json.dumps({"currency": "USD", "prices": {"m5.2xlarge": 0.25}}))
    assert (
        main(["estimate", "terraform", str(plan), "--prices", str(prices), "--format", "json"]) == 0
    )
    out = json.loads(capsys.readouterr().out)
    assert next(x for x in out["lines"] if x["address"] == "aws_instance.web")["source"] == "bill"
    assert main(["estimate", "terraform", str(plan), "--fail-above", "10"]) == 2
    capsys.readouterr()
    manifest = tmp_path / "app.yaml"
    manifest.write_text(
        "kind: Deployment\nmetadata: {name: a}\nspec:\n  replicas: 1\n  template:\n    spec:\n"
        "      containers: [{name: c, resources: {requests: {cpu: '1', memory: 2Gi}}}]\n"
    )
    assert main(["estimate", "kubernetes", str(manifest), "--fail-above", "1000"]) == 0
    assert "Deployment/default/a" in capsys.readouterr().out
