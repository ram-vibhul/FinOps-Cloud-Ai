from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select

from costlens.aws_audit.service import run_audit
from costlens.costs.ingest import CostLine, replace_billing_period
from costlens.db import Database
from costlens.db.base import utcnow
from costlens.db.tenant import set_tenant
from costlens.findings.service import run
from costlens.inventory import aws_platform as pl
from costlens.inventory.aws import Found
from costlens.inventory.service import upsert
from costlens.models import AuditResult, Finding
from tests.helpers import make_org


class Fake:
    def list_distributions(self, **kw: Any) -> dict[str, Any]:
        return {
            "DistributionList": {
                "Items": [
                    {
                        "Id": "E1",
                        "ARN": "arn:cf:E1",
                        "DomainName": "d.cloudfront.net",
                        "Enabled": True,
                        "PriceClass": "PriceClass_All",
                    }
                ],
                "IsTruncated": False,
            }
        }

    def list_hosted_zones(self, **kw: Any) -> dict[str, Any]:
        return {
            "HostedZones": [
                {"Id": "/hostedzone/Z1", "Name": "old.example.", "ResourceRecordSetCount": 2}
            ]
        }

    def describe_vpn_connections(self) -> dict[str, Any]:
        return {
            "VpnConnections": [
                {
                    "VpnConnectionId": "vpn-1",
                    "State": "available",
                    "VgwTelemetry": [{"Status": "DOWN"}, {"Status": "DOWN"}],
                }
            ]
        }

    def describe_transit_gateway_attachments(self, **kw: Any) -> dict[str, Any]:
        return {
            "TransitGatewayAttachments": [
                {
                    "TransitGatewayAttachmentId": "tgw-attach-1",
                    "State": "available",
                    "ResourceType": "vpc",
                }
            ]
        }

    def describe_log_groups(self, **kw: Any) -> dict[str, Any]:
        return {
            "logGroups": [
                {
                    "logGroupName": "/app",
                    "arn": "arn:logs:/app:*",
                    "storedBytes": 5,
                    "creationTime": 1_700_000_000_000,
                }
            ]
        }

    def list_keys(self, **kw: Any) -> dict[str, Any]:
        return {"Keys": [{"KeyId": "k1", "KeyArn": "arn:k1"}, {"KeyId": "k2", "KeyArn": "arn:k2"}]}

    def describe_key(self, KeyId: str) -> dict[str, Any]:
        return {
            "KeyMetadata": {
                "Arn": f"arn:{KeyId}",
                "KeyState": "Disabled",
                "KeyManager": "CUSTOMER" if KeyId == "k1" else "AWS",
            }
        }

    def list_web_acls(self, Scope: str, **kw: Any) -> dict[str, Any]:
        return {"WebACLs": [{"ARN": f"arn:waf:{Scope}", "Name": Scope.lower()}]}

    def list_resources_for_web_acl(self, WebACLArn: str) -> dict[str, Any]:
        return {"ResourceArns": []}

    def describe_workspaces_connection_status(self, **kw: Any) -> dict[str, Any]:
        return {"WorkspacesConnectionStatus": [{"WorkspaceId": "ws-1"}]}

    def describe_workspaces(self, **kw: Any) -> dict[str, Any]:
        return {
            "Workspaces": [
                {
                    "WorkspaceId": "ws-1",
                    "UserName": "ravi",
                    "State": "AVAILABLE",
                    "WorkspaceProperties": {"RunningMode": "ALWAYS_ON"},
                }
            ]
        }


def test_collectors() -> None:
    f = Fake()
    assert list(pl.cloudfront_distributions(f, "eu-west-1")) == []  # global: us-east-1 only
    (cf,) = pl.cloudfront_distributions(f, "us-east-1")
    assert cf.region == "global" and cf.attributes["price_class"] == "PriceClass_All"
    (zone,) = pl.route53_zones(f, "us-east-1")
    assert zone.resource_id == "Z1" and zone.attributes["records"] == 2
    vpn, att = pl.network_links(f, "r")
    assert (
        vpn.attributes == {"tunnels_up": 0, "tunnels": 2} and att.resource_type == "tgw_attachment"
    )
    (group,) = pl.log_groups(f, "r")
    assert group.resource_id == "arn:logs:/app" and group.created_at == datetime(
        2023, 11, 14, 22, 13, 20, tzinfo=UTC
    )
    assert [k.resource_id for k in pl.kms_keys(f, "r")] == ["arn:k1"]  # AWS-managed keys are free
    assert [a.attributes["scope"] for a in pl.waf_acls(f, "us-east-1")] == [
        "REGIONAL",
        "CLOUDFRONT",
    ]
    assert [a.attributes["resources"] for a in pl.waf_acls(f, "eu-west-1")] == [0]
    (ws,) = pl.workspaces(f, "r")
    assert ws.attributes["running_mode"] == "ALWAYS_ON" and ws.attributes["last_connection"] == ""


def test_global_resources_collected_from_us_east_1(db: Database) -> None:
    from costlens.inventory.service import collect_aws
    from costlens.models import CloudConnection, CloudResource
    from tests.fakes.aws import FakeAws

    org_id, _ = make_org(db, "Acme", "o@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        conn = CloudConnection(
            org_id=org_id,
            provider="aws",
            name="p",
            status="active",
            external_id="x",
            role_arn="arn:aws:iam::111111111111:role/r",
            account_id="111111111111",
        )
        s.add(conn)
        s.flush()
        conn_id = conn.id
    aws = FakeAws(clients={"cloudfront": Fake(), "route53": Fake()})
    aws.allow("arn:aws:iam::111111111111:role/r", "x")
    with db.session() as s:
        counts = collect_aws(s, aws, conn_id)
    assert counts["cloudfront_distributions"] == 1 and counts["route53_zones"] == 1
    assert ("client", ("cloudfront", "us-east-1")) in aws.calls
    aws.clients["route53"] = type(
        "Empty", (), {"list_hosted_zones": lambda self, **kw: {"HostedZones": []}}
    )()
    with db.session() as s:
        collect_aws(s, aws, conn_id)
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        states = {r.resource_id: r.state for r in s.scalars(select(CloudResource))}
    assert states["Z1"] == "deleted" and states["arn:cf:E1"] == "enabled"


def test_detectors_and_audit_rows(db: Database, monkeypatch) -> None:
    from costlens.findings import aws_compute

    monkeypatch.setattr(aws_compute, "_usd", lambda db, org_id, currency: 1.0)
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    old = utcnow() - timedelta(days=360)
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        upsert(
            s,
            org_id,
            "1",
            [
                Found(
                    "CloudWatch",
                    "log_group",
                    "arn:logs:big",
                    "r",
                    name="/big",
                    state="active",
                    created_at=old,
                    attributes={"retention_days": None, "stored_bytes": 400e9},
                ),
                Found(
                    "CloudWatch",
                    "log_group",
                    "arn:logs:kept",
                    "r",
                    name="/kept",
                    state="active",
                    created_at=old,
                    attributes={"retention_days": 30, "stored_bytes": 400e9},
                ),
                Found(
                    "CloudWatch",
                    "log_group",
                    "arn:logs:new",
                    "r",
                    name="/new",
                    state="active",
                    created_at=utcnow() - timedelta(days=20),
                    attributes={"stored_bytes": 400e9},
                ),
                Found(
                    "VPC",
                    "vpn_connection",
                    "vpn-1",
                    "r",
                    name="old-office",
                    state="available",
                    attributes={"tunnels": 2, "tunnels_up": 0},
                ),
                Found(
                    "VPC",
                    "vpn_connection",
                    "vpn-2",
                    "r",
                    name="dc",
                    state="available",
                    attributes={"tunnels": 2, "tunnels_up": 1},
                ),
                Found(
                    "WAF",
                    "waf_acl",
                    "arn:waf:1",
                    "r",
                    name="spare",
                    state="active",
                    attributes={"scope": "REGIONAL", "resources": 0},
                ),
                Found(
                    "CloudFront",
                    "cloudfront_distribution",
                    "arn:cf",
                    "global",
                    name="cdn",
                    state="enabled",
                    attributes={"price_class": "PriceClass_All"},
                ),
                Found(
                    "Route 53",
                    "route53_zone",
                    "Z1",
                    "global",
                    name="old.example.",
                    state="active",
                    attributes={"records": 2},
                ),
                Found(
                    "CloudWatch",
                    "cw_alarm",
                    "arn:alarm",
                    "r",
                    name="ghost",
                    state="insufficient_data",
                ),
                *[
                    Found(
                        "CloudWatch", "cw_dashboard", f"arn:d{i}", "r", name=f"d{i}", state="active"
                    )
                    for i in range(5)
                ],
                Found("KMS", "kms_key", "arn:k", "r", name="legacy key", state="disabled"),
                Found(
                    "Secrets Manager",
                    "secret",
                    "arn:s",
                    "r",
                    name="old-token",
                    state="active",
                    attributes={"last_accessed": ""},
                ),
                *[
                    Found(
                        "CloudTrail",
                        "cloudtrail_trail",
                        f"arn:t{i}",
                        "r",
                        name=f"t{i}",
                        state="active",
                        attributes={"multi_region": True},
                    )
                    for i in range(2)
                ],
                Found(
                    "WorkSpaces",
                    "workspace",
                    "ws-1",
                    "r",
                    name="ravi",
                    state="available",
                    attributes={"running_mode": "ALWAYS_ON", "last_connection": ""},
                ),
            ],
        )
    today = utcnow().date()
    lines = [
        CostLine(
            charge_date=today - timedelta(days=d),
            provider="aws",
            service_name=svc,
            usage_type=usage,
            billed_cost=Decimal(c),
            effective_cost=Decimal(c),
            billing_currency="USD",
        )
        for d in range(1, 31)
        for svc, usage, c in (
            ("Amazon Virtual Private Cloud", "USE1-PublicIPv4:IdleAddress", "0.5"),
            ("Amazon Virtual Private Cloud", "USE1-PublicIPv4:InUseAddress", "3"),
            ("AmazonCloudWatch", "USE1-DataProcessing-Bytes", "4"),
            ("AmazonCloudWatch", "USE1-CW:MetricMonitorUsage", "2"),
            ("AWS X-Ray", "USE1-XRay-TracesStored", "2"),
        )
    ]
    with db.session() as s:
        for month in sorted({ln.charge_date.replace(day=1) for ln in lines}):
            replace_billing_period(
                s,
                org_id=org_id,
                source_kind="t",
                source_key=str(month),
                billing_period=month,
                lines=[ln for ln in lines if ln.charge_date.replace(day=1) == month],
            )
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        run(s, org_id, only=["aws.log_retention", "aws.vpn_down", "aws.waf_unused"])
        rows = {f.dedup_key: f for f in s.scalars(select(Finding))}
        audit = run_audit(s, org_id, run_detectors=False)
        checks = {
            r.check_id: r
            for r in s.scalars(select(AuditResult).where(AuditResult.run_id == audit.id))
        }
    big = rows["aws.log_retention:arn:logs:big"]
    assert float(big.savings_monthly) == pytest.approx(400 * (1 - 90 / 360) * 0.03, abs=0.01)
    assert {k for k in rows if k.startswith("aws.log_retention")} == {
        "aws.log_retention:arn:logs:big"
    }
    assert float(rows["aws.vpn_down:vpn-1"].savings_monthly) == pytest.approx(
        0.05 * 24 * 30.4, abs=0.01
    )
    assert "aws.vpn_down:vpn-2" not in rows
    assert float(rows["aws.waf_unused:arn:waf:1"].savings_monthly) == 5.0
    for check in ("cloudwatch.logs", "network.vpn", "security.waf"):
        assert checks[check].status == "saving", check
    expect = {
        "network.public_ipv4": "Idle public IPv4",
        "cloudfront.config": "PriceClass_100",
        "route53.zones": "old.example.",
        "cloudwatch.metrics": "ghost",
        "xray.sampling": "sampling",
        "security.kms_secrets": "old-token",
        "security.cloudtrail": "only the first copy",
        "euc.workspaces": "ravi",
    }
    for check, text in expect.items():
        assert checks[check].status == "manual" and text in checks[check].reason, (
            check,
            checks[check].reason,
        )
    metrics_reason = checks["cloudwatch.metrics"].reason
    assert "5 dashboards" in metrics_reason and "Custom metrics cost" in metrics_reason
    assert checks["devtools.builds"].status == "fine"  # not used
