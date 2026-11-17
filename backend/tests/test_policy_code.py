"""Policy as code (11.41): the same policies before deploy, live, and as YAML."""

from __future__ import annotations

from fastapi.testclient import TestClient

from ccradar.db import Database
from tests.helpers import make_org, sign_in_client

POLICIES = """
policies:
  - name: No huge servers
    kind: denied_instance_types
    severity: critical
    params: {patterns: ["*.24xlarge", "*.metal"]}
  - name: India and Singapore only
    kind: allowed_regions
    severity: warning
    params: {regions: [ap-south-1, ap-southeast-1]}
  - name: Owner tag
    kind: required_tags
    params: {tags: [team]}
    resource_types: [ec2_instance]
  - name: Ask before big plans
    kind: max_monthly_increase
    severity: warning
    params: {amount: 1000}
"""


def plan(instance_type: str, tags: dict | None = None, region: str = "us-west-2") -> dict:
    return {
        "configuration": {
            "provider_config": {"aws": {"expressions": {"region": {"constant_value": region}}}}
        },
        "resource_changes": [
            {"address": "aws_instance.big", "type": "aws_instance",
             "change": {"actions": ["create"], "before": None,
                        "after": {"instance_type": instance_type, "tags": tags or {}}}},
            {"address": "aws_s3_bucket.logs", "type": "aws_s3_bucket",
             "change": {"actions": ["create"], "after": {}}},
        ],
    }  # fmt: skip


def test_policies_round_trip_as_yaml_and_check_plans(client: TestClient, db: Database) -> None:
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    c = sign_in_client(client, db, owner, org_id)
    put = c.put("/api/v1/governance/policies.yaml", content=POLICIES)
    assert put.json() == {"created": 4, "updated": 0, "deleted": 0}
    exported = c.get("/api/v1/governance/policies.yaml").text
    assert "No huge servers" in exported and "'*.24xlarge'" in exported
    first_two = POLICIES.split("  - name: Owner tag", maxsplit=1)[0]
    again = c.put("/api/v1/governance/policies.yaml", params={"prune": True}, content=first_two)
    assert again.json() == {"created": 0, "updated": 2, "deleted": 2}
    negative = "policies:\n  - {name: x, kind: max_monthly_increase, params: {amount: -1}}"
    bad = c.put("/api/v1/governance/policies.yaml", content=negative)
    assert bad.json()["code"] == "bad_policy"
    c.put("/api/v1/governance/policies.yaml", content=POLICIES)

    out = c.post("/api/v1/estimates/terraform", json=plan("m5.24xlarge")).json()
    found = {(v["policy"], v["resource"]) for v in out["policy"]["violations"]}
    assert found == {
        ("No huge servers", "aws_instance.big"),
        ("India and Singapore only", "aws_instance.big"),
        ("Owner tag", "aws_instance.big"),
        ("Ask before big plans", "whole plan"),  # 24xlarge is several thousand a month
    }
    assert out["policy"]["blocked"] is True and "**Blocked by policy**" in out["markdown"]
    fine = c.post("/api/v1/estimates/terraform",
                  json=plan("t3.small", {"team": "web"}, "ap-south-1")).json()  # fmt: skip
    assert fine["policy"] == {"violations": [], "blocked": False}
    assert "Policy" not in fine["markdown"]
