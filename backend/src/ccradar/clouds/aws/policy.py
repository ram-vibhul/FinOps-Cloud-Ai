"""The read-only IAM policy customers grant CloudCostRadar, and the templates that create it.

Least privilege: billing and cost data, resource *metadata* and metrics, AWS's own
recommendations. Never object contents, secrets, or anything that changes state.
The CUR bucket is the one exception for reading objects, limited to the export prefix.
"""

from __future__ import annotations

import json
from typing import Any

ROLE_NAME = "CloudCostRadarReadOnly"

READ_ACTIONS: list[str] = sorted(
    {
        # Billing and cost data
        "ce:Describe*",
        "ce:Get*",
        "ce:List*",
        "cur:DescribeReportDefinitions",
        "bcm-data-exports:GetExport",
        "bcm-data-exports:ListExports",
        "billing:GetBillingData",
        "billing:GetBillingDetails",
        "pricing:DescribeServices",
        "pricing:GetProducts",
        "pricing:GetAttributeValues",
        "savingsplans:Describe*",
        "organizations:Describe*",
        "organizations:List*",
        "account:GetAlternateContact",
        "account:ListRegions",
        # Recommendations from AWS
        "compute-optimizer:Get*",
        "compute-optimizer:Describe*",
        "cost-optimization-hub:Get*",
        "cost-optimization-hub:List*",
        "support:DescribeTrustedAdvisorCheckResult",
        "support:DescribeTrustedAdvisorChecks",
        "trustedadvisor:Describe*",
        "trustedadvisor:List*",
        # Resource metadata and metrics (no data plane access)
        "ec2:Describe*",
        "autoscaling:Describe*",
        "elasticloadbalancing:Describe*",
        "rds:Describe*",
        "rds:ListTagsForResource",
        "elasticache:Describe*",
        "elasticache:ListTagsForResource",
        "es:Describe*",
        "es:ListDomainNames",
        "es:ListTags",
        "redshift:Describe*",
        "dms:DescribeReplicationInstances",
        "dms:DescribeReplicationTasks",
        "dynamodb:DescribeTable",
        "dynamodb:ListTables",
        "dynamodb:ListTagsOfResource",
        "eks:Describe*",
        "eks:List*",
        "ecs:Describe*",
        "ecs:List*",
        "batch:DescribeComputeEnvironments",
        "ecr:DescribeRepositories",
        "ecr:DescribeImages",
        "ecr:GetLifecyclePolicy",
        "lambda:GetFunctionConfiguration",
        "lambda:ListFunctions",
        "lambda:ListProvisionedConcurrencyConfigs",
        "s3:GetBucketLocation",
        "s3:GetBucketTagging",
        "s3:GetLifecycleConfiguration",
        "s3:GetBucketVersioning",
        "s3:GetIntelligentTieringConfiguration",
        "s3:ListAllMyBuckets",
        "s3:GetStorageLensConfiguration",
        "s3:ListStorageLensConfigurations",
        "elasticfilesystem:Describe*",
        "fsx:Describe*",
        "backup:List*",
        "backup:Describe*",
        "backup:GetBackupPlan",
        "cloudwatch:GetMetricData",
        "cloudwatch:GetMetricStatistics",
        "cloudwatch:ListMetrics",
        "cloudwatch:DescribeAlarms",
        "logs:DescribeLogGroups",
        "cloudfront:ListDistributions",
        "route53:ListHostedZones",
        "kms:ListKeys",
        "kms:ListAliases",
        "secretsmanager:ListSecrets",  # names and metadata only, never values
        "sagemaker:List*",
        "sagemaker:Describe*",
        "bedrock:List*",
        "bedrock:Get*",
        "athena:ListWorkGroups",
        "athena:GetWorkGroup",
        "glue:GetJobs",
        "glue:GetCrawlers",
        "elasticmapreduce:ListClusters",
        "elasticmapreduce:DescribeCluster",
        "sqs:ListQueues",
        "sqs:GetQueueAttributes",
        "states:ListStateMachines",
        "kinesis:ListStreams",
        "kinesis:DescribeStreamSummary",
        "kafka:ListClustersV2",
        "mq:ListBrokers",
        "invoicing:ListInvoiceSummaries",
        "directconnect:DescribeConnections",
        "cloudwatch:ListDashboards",
        "kms:DescribeKey",
        "wafv2:ListWebACLs",
        "wafv2:ListResourcesForWebACL",
        "cloudtrail:DescribeTrails",
        "workspaces:DescribeWorkspaces",
        "workspaces:DescribeWorkspacesConnectionStatus",
        "tag:GetResources",
        "tag:GetTagKeys",
        "tag:GetTagValues",
        "cloudtrail:LookupEvents",
        "config:Describe*",
        "sts:GetCallerIdentity",
    }
)

# Verbs that would change something or read data contents; must never appear.
FORBIDDEN_VERB_PREFIXES = (
    "Create",
    "Delete",
    "Put",
    "Update",
    "Modify",
    "Start",
    "Stop",
    "Terminate",
    "Reboot",
    "Attach",
    "Detach",
    "Run",
    "Invoke",
    "Purchase",
    "Accept",
    "Reject",
    "Set",
    "Tag",
    "Untag",
    "Enable",
    "Disable",
    "Restore",
    "Associate",
    "Disassociate",
    "GetSecretValue",
    "Decrypt",
)


def policy_document(
    cur_bucket: str | None = None,
    cur_prefix: str | None = None,
    bedrock_bucket: str | None = None,
    bedrock_prefix: str | None = None,
) -> dict[str, Any]:
    statements: list[dict[str, Any]] = [
        {
            "Sid": "CloudCostRadarReadOnly",
            "Effect": "Allow",
            "Action": READ_ACTIONS,
            "Resource": "*",
        },
        {
            "Sid": "NoDataAccess",
            "Effect": "Deny",
            "Action": [
                "secretsmanager:GetSecretValue",
                "kms:Decrypt",
                "ssm:GetParameter*",
                "dynamodb:GetItem",
                "dynamodb:Query",
                "dynamodb:Scan",
                "rds-data:*",
                "lambda:InvokeFunction",
            ],
            "Resource": "*",
        },
    ]
    if cur_bucket:
        prefix = (cur_prefix or "").strip("/")
        objects = f"arn:aws:s3:::{cur_bucket}/{prefix + '/' if prefix else ''}*"
        statements.append(
            {
                "Sid": "ReadCostAndUsageExport",
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:ListBucket"],
                "Resource": [f"arn:aws:s3:::{cur_bucket}", objects],
            }
        )
    if bedrock_bucket:
        prefix = (bedrock_prefix or "").strip("/")
        objects = f"arn:aws:s3:::{bedrock_bucket}/{prefix + '/' if prefix else ''}AWSLogs/*"
        statements.append(
            {
                "Sid": "ReadBedrockInvocationLogs",
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:ListBucket"],
                "Resource": [f"arn:aws:s3:::{bedrock_bucket}", objects],
            }
        )
    return {"Version": "2012-10-17", "Statement": statements}


def trust_policy(principal_arn: str, external_id: str) -> dict[str, Any]:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"AWS": principal_arn},
                "Action": "sts:AssumeRole",
                "Condition": {"StringEquals": {"sts:ExternalId": external_id}},
            }
        ],
    }


def cloudformation_template(
    *,
    principal_arn: str,
    external_id: str,
    cur_bucket: str | None = None,
    cur_prefix: str | None = None,
    bedrock_bucket: str | None = None,
    bedrock_prefix: str | None = None,
) -> str:
    """CloudFormation (JSON is valid YAML) creating the read-only role."""
    template = {
        "AWSTemplateFormatVersion": "2010-09-09",
        "Description": "CloudCostRadar read-only access for cost and resource metadata.",
        "Resources": {
            "CloudCostRadarRole": {
                "Type": "AWS::IAM::Role",
                "Properties": {
                    "RoleName": ROLE_NAME,
                    "MaxSessionDuration": 3600,
                    "AssumeRolePolicyDocument": trust_policy(principal_arn, external_id),
                    "Policies": [
                        {
                            "PolicyName": "CloudCostRadarReadOnly",
                            "PolicyDocument": policy_document(
                                cur_bucket, cur_prefix, bedrock_bucket, bedrock_prefix
                            ),
                        }
                    ],
                    "Tags": [{"Key": "managed-by", "Value": "ccradar"}],
                },
            }
        },
        "Outputs": {"RoleArn": {"Value": {"Fn::GetAtt": ["CloudCostRadarRole", "Arn"]}}},
    }
    return json.dumps(template, indent=2)


def terraform_module(
    *,
    principal_arn: str,
    external_id: str,
    cur_bucket: str | None = None,
    cur_prefix: str | None = None,
    bedrock_bucket: str | None = None,
    bedrock_prefix: str | None = None,
) -> str:
    trust = json.dumps(trust_policy(principal_arn, external_id), indent=2)
    policy = json.dumps(
        policy_document(cur_bucket, cur_prefix, bedrock_bucket, bedrock_prefix), indent=2
    )
    return f"""# CloudCostRadar read-only role. Apply with: terraform init && terraform apply
resource "aws_iam_role" "ccradar" {{
  name                 = "{ROLE_NAME}"
  max_session_duration = 3600
  assume_role_policy   = <<-EOT
{trust}
  EOT
  tags = {{ "managed-by" = "ccradar" }}
}}

resource "aws_iam_role_policy" "ccradar" {{
  name   = "CloudCostRadarReadOnly"
  role   = aws_iam_role.ccradar.id
  policy = <<-EOT
{policy}
  EOT
}}

output "ccradar_role_arn" {{
  value = aws_iam_role.ccradar.arn
}}
"""
