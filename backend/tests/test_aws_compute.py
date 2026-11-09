from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select

from costlens.aws_audit.service import run_audit
from costlens.costs.ingest import CostLine, replace_billing_period
from costlens.db import Database
from costlens.db.base import utcnow
from costlens.db.tenant import set_tenant
from costlens.findings.aws_compute import eks_support
from costlens.findings.service import run
from costlens.inventory import aws_compute as c
from costlens.inventory.aws import Found
from costlens.inventory.service import upsert
from costlens.models import AuditResult, Finding
from tests.helpers import make_org
from tests.test_rightsizing_aws import add_metrics

NOW = datetime(2026, 11, 8, tzinfo=UTC)


class NotFound(Exception):
    def __init__(self) -> None:
        super().__init__("not found")
        self.response = {"Error": {"Code": "LifecyclePolicyNotFoundException"}}


class Fake:
    """One object answering every compute API the collectors call (two pages where paged)."""

    def list_functions(self, **kw: Any) -> dict[str, Any]:
        if "Marker" not in kw:
            return {
                "Functions": [
                    {
                        "FunctionArn": "arn:fn:a",
                        "FunctionName": "a",
                        "MemorySize": 512,
                        "Runtime": "python3.12",
                    }
                ],
                "NextMarker": "p2",
            }
        return {
            "Functions": [
                {
                    "FunctionArn": "arn:fn:b",
                    "FunctionName": "b",
                    "Architectures": ["arm64"],
                    "PackageType": "Image",
                }
            ]
        }

    def describe_auto_scaling_groups(self, **kw: Any) -> dict[str, Any]:
        return {
            "AutoScalingGroups": [
                {
                    "AutoScalingGroupARN": "arn:asg:web",
                    "AutoScalingGroupName": "web",
                    "MinSize": 2,
                    "MaxSize": 2,
                    "DesiredCapacity": 2,
                    "Instances": [{"InstanceId": "i-1", "InstanceType": "m6i.large"}],
                    "Tags": [
                        {"Key": "eks:nodegroup-name", "Value": "ng"},
                        {"Key": "eks:cluster-name", "Value": "k"},
                    ],
                    "MixedInstancesPolicy": {
                        "InstancesDistribution": {"OnDemandPercentageAboveBaseCapacity": 20}
                    },
                }
            ]
        }

    def list_clusters(self, **kw: Any) -> dict[str, Any]:
        return {"clusterArns": ["arn:ecs:cluster/dev"]}

    def list_services(self, **kw: Any) -> dict[str, Any]:
        return {"serviceArns": [f"arn:svc/{i}" for i in range(12)]}

    def describe_services(
        self, cluster: str, services: list[str], include: list[str]
    ) -> dict[str, Any]:
        assert len(services) <= 10 and include == ["TAGS"]
        return {
            "services": [
                {
                    "serviceArn": s,
                    "serviceName": s.rsplit("/", 1)[-1],
                    "launchType": "FARGATE",
                    "desiredCount": 1,
                    "runningCount": 1,
                    "status": "ACTIVE",
                    "tags": [{"key": "env", "value": "dev"}],
                }
                for s in services
            ]
        }

    def describe_compute_environments(self, **kw: Any) -> dict[str, Any]:
        return {
            "computeEnvironments": [
                {
                    "computeEnvironmentArn": "arn:batch:ce",
                    "computeEnvironmentName": "ce",
                    "type": "MANAGED",
                    "state": "ENABLED",
                    "computeResources": {"type": "EC2", "minvCpus": 4, "maxvCpus": 64},
                }
            ]
        }

    def describe_repositories(self, **kw: Any) -> dict[str, Any]:
        return {
            "repositories": [
                {"repositoryArn": "arn:ecr:app", "repositoryName": "app"},
                {"repositoryArn": "arn:ecr:ok", "repositoryName": "ok"},
            ]
        }

    def get_lifecycle_policy(self, repositoryName: str) -> dict[str, Any]:
        if repositoryName == "app":
            raise NotFound()
        return {"lifecyclePolicyText": "{}"}

    def describe_images(self, repositoryName: str, **kw: Any) -> dict[str, Any]:
        gib = 2**30
        images = [
            {
                "imagePushedAt": NOW - timedelta(days=d),
                "imageSizeInBytes": gib,
                "imageTags": [f"v{d}"],
            }
            for d in range(1, 16)
        ]  # 15 tagged, one a day
        images[0:0] = [
            {"imagePushedAt": NOW - timedelta(days=400), "imageSizeInBytes": gib}
        ]  # untagged
        images.append(
            {
                "imagePushedAt": NOW - timedelta(days=300),
                "imageSizeInBytes": gib,
                "imageTags": ["old"],
            }
        )
        return {"imageDetails": images}


class FakeEks:
    def list_clusters(self, **kw: Any) -> dict[str, Any]:
        return {"clusters": ["k"]}

    def describe_cluster(self, name: str) -> dict[str, Any]:
        return {
            "cluster": {
                "arn": "arn:eks:k",
                "version": "1.28",
                "status": "ACTIVE",
                "tags": {"env": "dev"},
            }
        }

    def list_nodegroups(self, clusterName: str, **kw: Any) -> dict[str, Any]:
        return {"nodegroups": ["ng"]}

    def describe_nodegroup(self, clusterName: str, nodegroupName: str) -> dict[str, Any]:
        return {
            "nodegroup": {
                "nodegroupArn": "arn:ng",
                "capacityType": "ON_DEMAND",
                "status": "ACTIVE",
                "scalingConfig": {"minSize": 1, "maxSize": 3, "desiredSize": 2},
            }
        }


def test_collectors_map_every_service() -> None:
    f = Fake()
    fns = list(c.lambda_functions(f, "eu-west-1"))
    assert [x.resource_id for x in fns] == ["arn:fn:a", "arn:fn:b"]
    assert (
        fns[0].attributes["architectures"] == ["x86_64"]
        and fns[1].attributes["package_type"] == "Image"
    )
    (asg,) = c.autoscaling_groups(f, "r")
    assert asg.attributes["eks_nodegroup"] == "ng" and asg.attributes["on_demand_pct"] == 20
    assert asg.attributes["instance_ids"] == ["i-1"]
    services = list(c.ecs_services(f, "r"))
    assert len(services) == 12 and services[0].attributes["cluster"] == "dev"
    assert services[0].tags == {"env": "dev"}
    (ce,) = c.batch_environments(f, "r")
    assert ce.attributes["min_vcpus"] == 4 and ce.attributes["compute_type"] == "EC2"
    repos = {r.name: r.attributes for r in c.ecr_repositories(f, "r", now=NOW)}
    assert repos["app"]["lifecycle_policy"] is False and repos["ok"]["lifecycle_policy"] is True
    gib = 2**30
    # untagged: 1 GiB; stale: tagged beyond the newest 10 and older than 180 days -> "old" only
    assert repos["app"]["untagged_bytes"] == gib and repos["app"]["stale_bytes"] == gib
    assert repos["app"]["image_count"] == 17
    eks = list(c.eks_clusters(FakeEks(), "r"))
    assert [(x.resource_type, x.attributes.get("capacity_type")) for x in eks] == [
        ("eks_cluster", None),
        ("eks_nodegroup", "ON_DEMAND"),
    ]
    assert eks[1].attributes["desired"] == 2 and eks[1].attributes["cluster"] == "k"


@pytest.mark.parametrize(
    ("version", "today", "expected"),
    [
        ("1.28", date(2026, 1, 1), "extended"),
        ("1.31", date(2026, 11, 8), "standard"),
        ("1.31", date(2026, 11, 27), "extended"),
        ("1.21", date(2026, 1, 1), "extended"),
        ("1.40", date(2026, 1, 1), "unknown"),
        ("weird", date(2026, 1, 1), "unknown"),
    ],
)
def test_eks_support_calendar(version: str, today: date, expected: str) -> None:
    assert eks_support(version, today) == expected


def _bill(db: Database, org_id, rows: list[tuple[str, str | None, float]]) -> None:
    today = utcnow().date()
    lines = [
        CostLine(
            charge_date=today - timedelta(days=d),
            provider="aws",
            service_name=svc,
            resource_id=rid,
            billed_cost=Decimal(str(cost)),
            effective_cost=Decimal(str(cost)),
            billing_currency="USD",
        )
        for d in range(1, 31)
        for svc, rid, cost in rows
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


def _seed(db: Database):
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    ec2 = "Amazon Elastic Compute Cloud"
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        node_tags = {
            "eks:cluster-name": "dev-k",
            "eks:nodegroup-name": "ng",
            "aws:autoscaling:groupName": "eks-ng",
        }
        upsert(
            s,
            org_id,
            "1",
            [
                Found(
                    "AWS Lambda",
                    "lambda_function",
                    "arn:fn:x86",
                    "us-east-1",
                    name="x86",
                    state="Active",
                    attributes={
                        "architectures": ["x86_64"],
                        "runtime": "python3.12",
                        "package_type": "Zip",
                    },
                ),
                Found(
                    "AWS Lambda",
                    "lambda_function",
                    "arn:fn:go",
                    "us-east-1",
                    name="go",
                    state="Active",
                    attributes={"architectures": ["x86_64"], "runtime": "go1.x"},
                ),
                Found(
                    "AWS Lambda",
                    "lambda_function",
                    "arn:fn:arm",
                    "us-east-1",
                    name="arm",
                    state="Active",
                    attributes={"architectures": ["arm64"], "runtime": "python3.12"},
                ),
                Found(
                    ec2,
                    "ec2_instance",
                    "i-web",
                    "us-east-1",
                    state="running",
                    tags={"aws:autoscaling:groupName": "web-dev"},
                ),
                Found(ec2, "ec2_instance", "i-node", "us-east-1", state="running", tags=node_tags),
                Found(
                    ec2,
                    "ec2_instance",
                    "i-idle",
                    "us-east-1",
                    state="running",
                    tags={"aws:autoscaling:groupName": "idle-prod"},
                    attributes={"instance_type": "m6i.large"},
                ),
                Found(
                    "Amazon EC2 Auto Scaling",
                    "autoscaling_group",
                    "arn:asg:web-dev",
                    "us-east-1",
                    name="web-dev",
                    state="active",
                    tags={"env": "dev"},
                    attributes={
                        "min": 2,
                        "max": 2,
                        "desired": 2,
                        "instance_ids": ["i-web"],
                        "on_demand_pct": 100,
                    },
                ),
                Found(
                    "Amazon EC2 Auto Scaling",
                    "autoscaling_group",
                    "arn:asg:idle",
                    "us-east-1",
                    name="idle-prod",
                    state="active",
                    attributes={
                        "min": 1,
                        "max": 4,
                        "desired": 1,
                        "instance_ids": ["i-idle"],
                        "on_demand_pct": 100,
                    },
                ),
                Found(
                    "Amazon EC2 Auto Scaling",
                    "autoscaling_group",
                    "arn:asg:eks",
                    "us-east-1",
                    name="eks-ng",
                    state="active",
                    attributes={
                        "min": 1,
                        "max": 3,
                        "desired": 1,
                        "instance_ids": ["i-node"],
                        "on_demand_pct": 100,
                        "eks_nodegroup": "ng",
                        "eks_cluster": "dev-k",
                    },
                ),
                Found(
                    "Amazon Elastic Kubernetes Service",
                    "eks_cluster",
                    "arn:eks:dev-k",
                    "us-east-1",
                    name="dev-k",
                    state="active",
                    attributes={"version": "1.27"},
                ),
                Found(
                    "Amazon Elastic Kubernetes Service",
                    "eks_nodegroup",
                    "arn:ng",
                    "us-east-1",
                    name="ng",
                    state="active",
                    attributes={"cluster": "dev-k", "capacity_type": "ON_DEMAND"},
                ),
                Found(
                    "Amazon ECR",
                    "ecr_repository",
                    "arn:ecr:app",
                    "us-east-1",
                    name="app",
                    state="active",
                    attributes={
                        "lifecycle_policy": False,
                        "untagged_bytes": 200 * 2**30,
                        "stale_bytes": 100 * 2**30,
                    },
                ),
                Found(
                    "Amazon ECR",
                    "ecr_repository",
                    "arn:ecr:tiny",
                    "us-east-1",
                    name="tiny",
                    state="active",
                    attributes={
                        "lifecycle_policy": False,
                        "untagged_bytes": 2**30,
                        "stale_bytes": 0,
                    },
                ),
                Found(
                    "Amazon Elastic Container Service",
                    "ecs_service",
                    "arn:svc:api",
                    "us-east-1",
                    name="api",
                    state="active",
                    tags={"env": "staging"},
                    attributes={"launch_type": "FARGATE", "desired": 2, "capacity_providers": []},
                ),
                Found(
                    "AWS Batch",
                    "batch_compute_environment",
                    "arn:ce",
                    "us-east-1",
                    name="nightly",
                    state="enabled",
                    attributes={"managed": True, "compute_type": "SPOT", "min_vcpus": 8},
                ),
            ],
        )
        add_metrics(s, org_id, "i-idle", "cpu_pct", 2)
        add_metrics(s, org_id, "i-node", "cpu_pct", 1)
        add_metrics(s, org_id, "i-web", "cpu_pct", 60)
    _bill(
        db,
        org_id,
        [
            ("AWS Lambda", "arn:fn:x86", 5),
            ("AWS Lambda", "arn:fn:go", 5),
            ("AWS Lambda", "arn:fn:arm", 5),
            (ec2, "i-web", 10),
            (ec2, "i-node", 10),
            (ec2, "i-idle", 4),
            ("Amazon Elastic Kubernetes Service", "arn:eks:dev-k", 2.4),
            ("Amazon Elastic Container Service", None, 3),
            ("AWS Batch", None, 1),
            ("Amazon EC2 Container Registry (ECR)", None, 1),
        ],
    )
    return org_id


def test_compute_detectors(db: Database) -> None:
    org_id = _seed(db)
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        results = run(s, org_id)
        assert not [k for k, v in results.items() if "error" in v]
        rows = {f.dedup_key: f for f in s.scalars(select(Finding))}
    month = 30.4
    arm = rows["aws.lambda_arm64:arn:fn:x86"]
    assert (
        float(arm.savings_monthly) == pytest.approx(5 * month * 0.2, abs=0.01)
        and arm.effort == "low"
    )
    assert not any(
        k.startswith("aws.lambda_arm64:arn:fn:go") or k.endswith("arn:fn:arm") for k in rows
    )
    web = rows["aws.spot_nonprod_capacity:arn:asg:web-dev"]
    assert float(web.savings_monthly) == pytest.approx(10 * month * 0.6, abs=0.01)
    ng = rows["aws.spot_nonprod_capacity:arn:ng"]  # non-prod via the cluster name
    assert ng.effort == "medium" and "cluster-name dev-k" in ng.commands[0]
    assert "aws.spot_nonprod_capacity:arn:asg:eks" not in rows  # judged as the node group
    idle = rows["aws.idle_asgs:arn:asg:idle"]
    assert float(idle.savings_monthly) == pytest.approx(4 * month, abs=0.01)
    assert "--min-size 0" in idle.commands[0]
    assert "aws.idle_asgs:arn:asg:web-dev" not in rows  # busy
    node = rows["aws.eks_idle_nodegroups:arn:asg:eks"]
    assert "update-nodegroup-config" in node.commands[0] and "EKS node group ng" in node.title
    eks = rows["aws.eks_extended_support:arn:eks:dev-k"]
    assert eks.evidence["basis"] == "list price" and float(eks.savings_monthly) == pytest.approx(
        0.5 * 24 * month, abs=0.01
    )
    ecr = rows["aws.ecr_cleanup:arn:ecr:app"]
    assert float(ecr.savings_monthly) == pytest.approx(30.0, abs=0.01)
    assert "aws.ecr_cleanup:arn:ecr:tiny" not in rows  # 0.10 a month: not worth a finding
    # Instances in Auto Scaling groups are judged per group, never stopped one by one.
    assert not [k for k in rows if k.startswith("aws.ec2_rightsizing:")]


def test_audit_rows_for_compute(db: Database) -> None:
    org_id = _seed(db)
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        audit = run_audit(s, org_id)
        rows = {
            r.check_id: r
            for r in s.scalars(select(AuditResult).where(AuditResult.run_id == audit.id))
        }
    for check in (
        "lambda.config",
        "ec2.spot",
        "autoscaling.groups",
        "eks.versions",
        "eks.nodegroups",
        "ecr.lifecycle",
    ):
        assert rows[check].status == "saving", (check, rows[check].reason)
    fargate = rows["ecs.fargate"]
    assert (
        fargate.status == "manual" and "Fargate Spot" in fargate.reason and "api" in fargate.reason
    )
    batch = rows["batch.compute"]
    assert batch.status == "manual" and "minimum vCPUs above 0" in batch.reason
    assert "On-Demand" not in batch.reason  # it already uses Spot
