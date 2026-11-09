"""Check which read-only permissions a connection actually has, and what is lost without them.

Each probe is a harmless read call (small page sizes). Results:
``ok``, ``missing`` (access denied), ``unavailable`` (the account cannot use the feature,
e.g. Trusted Advisor needs a Business support plan, Compute Optimizer is not enrolled),
or ``error`` (anything else, e.g. throttling). Cost Explorer is not probed: every call
costs about USD 0.01 and imports already exercise it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from costlens.clouds.aws.client import AwsAccessError, AwsGateway
from costlens.db.base import utcnow
from costlens.models import CloudConnection

DENIED = {
    "AccessDenied",
    "AccessDeniedException",
    "UnauthorizedOperation",
    "UnauthorizedAccess",
    "AuthorizationError",
    "Forbidden",
}
UNAVAILABLE = {
    "SubscriptionRequiredException",
    "OptInRequiredException",
    "OptInRequired",
    "AWSOrganizationsNotInUseException",
    "NotEnrolled",
}


@dataclass(frozen=True)
class Probe:
    permission: str
    service: str
    method: str
    feature: str
    args: dict[str, Any]
    region: str | None = None
    needs_bucket: bool = False


PROBES: list[Probe] = [
    Probe(
        "organizations:DescribeOrganization",
        "organizations",
        "describe_organization",
        "Linked account names and payer detection",
        {},
    ),
    Probe(
        "bcm-data-exports:ListExports",
        "bcm-data-exports",
        "list_exports",
        "Finding your billing exports (CUR 2.0 / FOCUS)",
        {"MaxResults": 1},
        region="us-east-1",
    ),
    Probe(
        "s3:ListBucket (billing export)",
        "s3",
        "list_objects_v2",
        "Resource-level costs from the billing export",
        {"MaxKeys": 1},
        needs_bucket=True,
    ),
    Probe(
        "ec2:DescribeInstances",
        "ec2",
        "describe_instances",
        "Resource inventory, idle and stopped instances",
        {"MaxResults": 5},
    ),
    Probe(
        "ec2:DescribeVolumes", "ec2", "describe_volumes", "Unattached volumes", {"MaxResults": 5}
    ),
    Probe("ec2:DescribeAddresses", "ec2", "describe_addresses", "Idle IP addresses", {}),
    Probe(
        "ec2:DescribeNatGateways",
        "ec2",
        "describe_nat_gateways",
        "Idle NAT gateways",
        {"MaxResults": 5},
    ),
    Probe(
        "ec2:DescribeSnapshots",
        "ec2",
        "describe_snapshots",
        "Old snapshots",
        {"OwnerIds": ["self"], "MaxResults": 5},
    ),
    Probe(
        "elasticloadbalancing:DescribeLoadBalancers",
        "elbv2",
        "describe_load_balancers",
        "Unused load balancers",
        {"PageSize": 1},
    ),
    Probe(
        "rds:DescribeDBInstances",
        "rds",
        "describe_db_instances",
        "Database checks",
        {"MaxRecords": 20},
    ),
    Probe(
        "lambda:ListFunctions",
        "lambda",
        "list_functions",
        "Lambda architecture checks",
        {"MaxItems": 1},
    ),
    Probe(
        "autoscaling:DescribeAutoScalingGroups",
        "autoscaling",
        "describe_auto_scaling_groups",
        "Auto Scaling groups: idle groups and Spot",
        {"MaxRecords": 1},
    ),
    Probe("ecs:ListClusters", "ecs", "list_clusters", "ECS and Fargate checks", {"maxResults": 1}),
    Probe(
        "batch:DescribeComputeEnvironments",
        "batch",
        "describe_compute_environments",
        "AWS Batch checks",
        {"maxResults": 1},
    ),
    Probe(
        "ecr:DescribeRepositories",
        "ecr",
        "describe_repositories",
        "Container image clean-up",
        {"maxResults": 1},
    ),
    Probe(
        "eks:ListClusters",
        "eks",
        "list_clusters",
        "EKS versions and node groups",
        {"maxResults": 1},
    ),
    Probe(
        "s3:ListAllMyBuckets",
        "s3",
        "list_buckets",
        "S3 lifecycle and Intelligent-Tiering checks",
        {"MaxBuckets": 1},
    ),
    Probe(
        "elasticfilesystem:DescribeFileSystems",
        "efs",
        "describe_file_systems",
        "EFS lifecycle checks",
        {"MaxItems": 1},
    ),
    Probe(
        "fsx:DescribeFileSystems", "fsx", "describe_file_systems", "FSx checks", {"MaxResults": 1}
    ),
    Probe(
        "backup:ListBackupPlans",
        "backup",
        "list_backup_plans",
        "Backup retention checks",
        {"MaxResults": 1},
    ),
    Probe(
        "rds:DescribeDBClusters",
        "rds",
        "describe_db_clusters",
        "Aurora, DocumentDB and Neptune checks",
        {"MaxRecords": 20},
    ),
    Probe("dynamodb:ListTables", "dynamodb", "list_tables", "DynamoDB checks", {"Limit": 1}),
    Probe(
        "elasticache:DescribeCacheClusters",
        "elasticache",
        "describe_cache_clusters",
        "ElastiCache checks",
        {"MaxRecords": 20},
    ),
    Probe("es:ListDomainNames", "opensearch", "list_domain_names", "OpenSearch checks", {}),
    Probe(
        "redshift:DescribeClusters",
        "redshift",
        "describe_clusters",
        "Redshift checks",
        {"MaxRecords": 20},
    ),
    Probe(
        "dms:DescribeReplicationInstances",
        "dms",
        "describe_replication_instances",
        "Idle DMS replication instances",
        {"MaxRecords": 20},
    ),
    Probe(
        "sagemaker:ListEndpoints",
        "sagemaker",
        "list_endpoints",
        "SageMaker checks",
        {"MaxResults": 1},
    ),
    Probe(
        "bedrock:ListProvisionedModelThroughputs",
        "bedrock",
        "list_provisioned_model_throughputs",
        "Bedrock provisioned throughput",
        {"maxResults": 1},
    ),
    Probe("glue:GetJobs", "glue", "get_jobs", "Glue checks", {"MaxResults": 1}),
    Probe(
        "athena:ListWorkGroups", "athena", "list_work_groups", "Athena checks", {"MaxResults": 1}
    ),
    Probe("elasticmapreduce:ListClusters", "emr", "list_clusters", "EMR checks", {}),
    Probe("sqs:ListQueues", "sqs", "list_queues", "SQS checks", {"MaxResults": 1}),
    Probe(
        "cloudwatch:ListMetrics",
        "cloudwatch",
        "list_metrics",
        "Usage metrics for right-sizing",
        {"Namespace": "AWS/EC2"},
    ),
    Probe(
        "savingsplans:DescribeSavingsPlans",
        "savingsplans",
        "describe_savings_plans",
        "Savings Plans coverage and utilisation",
        {"maxResults": 1},
        region="us-east-1",
    ),
    Probe(
        "compute-optimizer:GetEnrollmentStatus",
        "compute-optimizer",
        "get_enrollment_status",
        "AWS Compute Optimizer recommendations",
        {},
    ),
    Probe(
        "cost-optimization-hub:ListEnrollmentStatuses",
        "cost-optimization-hub",
        "list_enrollment_statuses",
        "AWS Cost Optimization Hub recommendations",
        {"maxResults": 1},
        region="us-east-1",
    ),
    Probe(
        "support:DescribeTrustedAdvisorChecks",
        "support",
        "describe_trusted_advisor_checks",
        "Trusted Advisor checks",
        {"language": "en"},
        region="us-east-1",
    ),
    Probe(
        "cloudtrail:LookupEvents",
        "cloudtrail",
        "lookup_events",
        "Change timeline (infrastructure changes)",
        {"MaxResults": 1},
    ),
    Probe("eks:ListClusters", "eks", "list_clusters", "Kubernetes clusters", {"maxResults": 1}),
]


def _error_code(exc: Exception) -> str:
    response = getattr(exc, "response", None) or {}
    return str((response.get("Error") or {}).get("Code") or type(exc).__name__)


def run(gateway: AwsGateway, conn: CloudConnection, *, region: str = "us-east-1") -> dict[str, Any]:
    try:
        session = gateway.assume(conn.role_arn or "", conn.external_id, f"costlens-{conn.org_id}")
    except AwsAccessError as exc:
        return {
            "checked_at": utcnow().isoformat(),
            "role": "error",
            "message": str(exc),
            "results": [],
            "missing": [],
            "policy_snippet": None,
        }
    results = []
    for probe in PROBES:
        if probe.needs_bucket and not conn.cur_bucket:
            results.append(
                {
                    "permission": probe.permission,
                    "feature": probe.feature,
                    "status": "skipped",
                    "message": "No billing export bucket set.",
                }
            )
            continue
        args = dict(probe.args)
        if probe.needs_bucket:
            args.update(Bucket=conn.cur_bucket, Prefix=(conn.cur_prefix or "").strip("/"))
        try:
            client = gateway.client(session, probe.service, region=probe.region or region)
            getattr(client, probe.method)(**args)
            status, message = "ok", ""
        except Exception as exc:  # botocore ClientError and friends
            code = _error_code(exc)
            if code in DENIED:
                status, message = "missing", f"{probe.feature} will not work."
            elif code in UNAVAILABLE:
                status, message = "unavailable", "Not available for this account (plan or opt-in)."
            else:
                status, message = "error", f"Could not check ({code})."
        results.append(
            {
                "permission": probe.permission,
                "feature": probe.feature,
                "status": status,
                "message": message,
            }
        )
    missing = [r["permission"] for r in results if r["status"] == "missing"]
    actions = sorted(
        {p.split(" ")[0] if not p.startswith("s3:") else "s3:ListBucket" for p in missing}
    )
    if "s3:ListBucket" in actions:
        actions.append("s3:GetObject")
    snippet = (
        json.dumps({"Effect": "Allow", "Action": actions, "Resource": "*"}, indent=2)
        if actions
        else None
    )
    return {
        "checked_at": utcnow().isoformat(),
        "role": "ok",
        "message": "",
        "results": results,
        "missing": missing,
        "policy_snippet": snippet,
    }
