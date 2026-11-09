from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select

from costlens.aws_audit.inspections import _often
from costlens.aws_audit.service import run_audit
from costlens.clouds.aws import metrics
from costlens.costs.ingest import CostLine, replace_billing_period
from costlens.db import Database
from costlens.db.base import utcnow
from costlens.db.tenant import set_tenant
from costlens.findings.service import run
from costlens.inventory import aws_analytics as an
from costlens.inventory.aws import Found
from costlens.inventory.service import upsert
from costlens.models import AuditResult, CloudResource, Finding, ResourceMetric
from tests.helpers import make_org
from tests.test_rightsizing_aws import add_metrics


class Fake:
    def list_work_groups(self, **kw: Any) -> dict[str, Any]:
        return {"WorkGroups": [{"Name": "primary", "State": "ENABLED"}]}

    def get_work_group(self, WorkGroup: str) -> dict[str, Any]:
        return {"WorkGroup": {"Configuration": {"EnforceWorkGroupConfiguration": True}}}

    def get_jobs(self, **kw: Any) -> dict[str, Any]:
        return {"Jobs": [{"Name": "etl", "Command": {"Name": "glueetl"}, "GlueVersion": "4.0"}]}

    def get_crawlers(self, **kw: Any) -> dict[str, Any]:
        return {
            "Crawlers": [
                {
                    "Name": "c",
                    "State": "READY",
                    "Schedule": {"ScheduleExpression": "cron(0 * * * ? *)"},
                }
            ]
        }

    def list_clusters(self, **kw: Any) -> dict[str, Any]:
        if "ClusterStates" in kw:  # EMR
            return {"Clusters": [{"Id": "j-1", "Name": "spark"}]}
        return {"ClusterInfoList": []}

    def describe_cluster(self, ClusterId: str) -> dict[str, Any]:
        return {
            "Cluster": {
                "ClusterArn": "arn:emr:j-1",
                "Status": {"State": "WAITING"},
                "AutoTerminate": False,
            }
        }

    def list_endpoints(self, **kw: Any) -> dict[str, Any]:
        return {
            "Endpoints": [
                {
                    "EndpointName": "model",
                    "EndpointArn": "arn:sm:model",
                    "EndpointStatus": "InService",
                }
            ]
        }

    def describe_endpoint(self, EndpointName: str) -> dict[str, Any]:
        return {
            "ProductionVariants": [
                {"VariantName": "a", "CurrentInstanceCount": 2},
                {"VariantName": "b", "CurrentInstanceCount": 1},
            ]
        }

    def list_notebook_instances(self, **kw: Any) -> dict[str, Any]:
        return {
            "NotebookInstances": [
                {
                    "NotebookInstanceName": "nb",
                    "NotebookInstanceArn": "arn:nb",
                    "NotebookInstanceStatus": "InService",
                }
            ]
        }

    def list_queues(self, **kw: Any) -> dict[str, Any]:
        return {"QueueUrls": ["https://sqs/1/jobs"]}

    def get_queue_attributes(self, QueueUrl: str, AttributeNames: list[str]) -> dict[str, Any]:
        return {"Attributes": {"QueueArn": "arn:sqs:jobs", "ReceiveMessageWaitTimeSeconds": "0"}}

    def list_streams(self, **kw: Any) -> dict[str, Any]:
        return {"StreamNames": ["clicks"]}

    def describe_stream_summary(self, StreamName: str) -> dict[str, Any]:
        return {
            "StreamDescriptionSummary": {
                "StreamARN": "arn:ks:clicks",
                "RetentionPeriodHours": 168,
                "OpenShardCount": 4,
            }
        }


def test_collectors() -> None:
    f = Fake()
    (wg,) = an.athena_workgroups(f, "r")
    assert wg.state == "enabled" and wg.attributes["scan_limit_bytes"] is None
    items = list(an.glue_jobs(f, "r"))
    assert [i.resource_type for i in items] == ["glue_job", "glue_crawler"]
    assert items[0].attributes["execution_class"] == "STANDARD"
    (emr,) = an.emr_clusters(f, "r")
    assert emr.state == "waiting" and emr.attributes["auto_terminate"] is False
    ep, nb = an.sagemaker_resources(f, "r")
    assert ep.attributes["variants"] == ["a", "b"] and ep.attributes["instances"] == 3
    assert nb.resource_type == "sagemaker_notebook" and nb.attributes["lifecycle_config"] is None
    (q,) = an.sqs_queues(f, "r")
    assert q.name == "jobs" and q.attributes["wait_seconds"] == 0
    (stream,) = an.kinesis_streams(f, "r")
    assert (
        stream.attributes["mode"] == "PROVISIONED" and stream.attributes["retention_hours"] == 168
    )


@pytest.mark.parametrize(
    ("schedule", "often"),
    [
        ("cron(0 * * * ? *)", True),
        ("cron(0 2 * * ? *)", False),
        ("cron(0 0/6 * * ? *)", True),
        ("rate(1 hour)", True),
        ("rate(1 day)", False),
        (None, False),
    ],
)
def test_crawler_frequency(schedule: str | None, often: bool) -> None:
    assert _often(schedule) is often


class FakeCloudWatch:
    def __init__(self) -> None:
        self.dims: list[list[dict[str, str]]] = []

    def get_metric_data(self, **kw: Any) -> dict[str, Any]:
        start = datetime(2026, 11, 1, tzinfo=UTC)
        out = []
        for q in kw["MetricDataQueries"]:
            dims = q["MetricStat"]["Metric"]["Dimensions"]
            self.dims.append(dims)
            busy = dims[-1]["Value"] == "b"
            out.append(
                {
                    "Id": q["Id"],
                    "Timestamps": [start + timedelta(hours=h) for h in range(3)],
                    "Values": [5.0 if busy else 0.0] * 3,
                }
            )
        return {"MetricDataResults": out}


def test_endpoint_invocations_keep_the_busiest_variant(db: Database) -> None:
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        upsert(
            s,
            org_id,
            "1",
            [
                Found(
                    "Amazon SageMaker",
                    "sagemaker_endpoint",
                    "arn:sm:e",
                    "r",
                    name="e",
                    state="InService",
                    attributes={"variants": ["a", "b"]},
                )
            ],
        )
        cw = FakeCloudWatch()
        metrics.collect(
            s,
            cw,
            org_id,
            list(s.scalars(select(CloudResource))),
            now=datetime(2026, 11, 2, tzinfo=UTC),
        )
        row = s.scalars(select(ResourceMetric)).one()
    assert row.metric == "invocations" and row.max == 5.0
    assert {d[0]["Name"] for d in cw.dims} == {"EndpointName"}


def test_detector_and_audit_rows(db: Database) -> None:
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        upsert(
            s,
            org_id,
            "1",
            [
                Found(
                    "Amazon SageMaker",
                    "sagemaker_endpoint",
                    "arn:sm:idle",
                    "r",
                    name="idle",
                    state="InService",
                    attributes={"instances": 2},
                ),
                Found(
                    "Amazon SageMaker",
                    "sagemaker_endpoint",
                    "arn:sm:busy",
                    "r",
                    name="busy",
                    state="InService",
                ),
                Found(
                    "Amazon SageMaker",
                    "sagemaker_notebook",
                    "arn:nb",
                    "r",
                    name="nb",
                    state="InService",
                ),
                Found(
                    "Amazon Athena",
                    "athena_workgroup",
                    "athena:r:primary",
                    "r",
                    name="primary",
                    state="enabled",
                ),
                Found(
                    "AWS Glue",
                    "glue_job",
                    "glue:r:job/etl",
                    "r",
                    name="etl",
                    state="active",
                    attributes={
                        "command": "glueetl",
                        "glue_version": "4.0",
                        "execution_class": "STANDARD",
                    },
                ),
                Found("Amazon EMR", "emr_cluster", "arn:emr", "r", name="spark", state="waiting"),
                Found(
                    "Amazon Bedrock",
                    "bedrock_provisioned_throughput",
                    "arn:pt",
                    "r",
                    name="pt",
                    state="inservice",
                    attributes={"commitment": None},
                ),
                Found(
                    "Amazon Bedrock",
                    "bedrock_knowledge_base",
                    "arn:kb",
                    "r",
                    name="docs",
                    state="active",
                    attributes={"vector_store": "OPENSEARCH_SERVERLESS"},
                ),
                Found(
                    "Amazon SQS",
                    "sqs_queue",
                    "arn:sqs:jobs",
                    "r",
                    name="jobs",
                    state="active",
                    attributes={"wait_seconds": 0},
                ),
                Found(
                    "AWS Step Functions",
                    "state_machine",
                    "arn:sf:orders",
                    "r",
                    name="orders",
                    state="active",
                    attributes={"type": "STANDARD"},
                ),
                Found(
                    "Amazon MQ",
                    "mq_broker",
                    "arn:mq",
                    "r",
                    name="events-dev",
                    state="running",
                    attributes={"deployment": "ACTIVE_STANDBY_MULTI_AZ"},
                ),
            ],
        )
        add_metrics(s, org_id, "arn:sm:idle", "invocations", 0)
        add_metrics(s, org_id, "arn:sm:busy", "invocations", 40)
    today = utcnow().date()
    lines = [
        CostLine(
            charge_date=today - timedelta(days=d),
            provider="aws",
            service_name="svc",
            resource_id=rid,
            usage_type=usage,
            billed_cost=Decimal(c),
            effective_cost=Decimal(c),
            billing_currency="USD",
        )
        for d in range(1, 31)
        for rid, usage, c in (
            ("arn:sm:idle", "Host:ml.m5.large", "12"),
            ("arn:sm:busy", "Host", "12"),
            ("arn:sf:orders", "StateTransition", "3"),
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
        run(s, org_id, only=["aws.sagemaker_idle_endpoints"])
        rows = {f.dedup_key: f for f in s.scalars(select(Finding))}
        audit = run_audit(s, org_id, run_detectors=False)
        checks = {
            r.check_id: r
            for r in s.scalars(select(AuditResult).where(AuditResult.run_id == audit.id))
        }
    idle = rows["aws.sagemaker_idle_endpoints:arn:sm:idle"]
    assert float(idle.savings_monthly) == pytest.approx(12 * 30.4, abs=0.01)
    assert "aws.sagemaker_idle_endpoints:arn:sm:busy" not in rows
    assert checks["sagemaker.endpoints"].status == "saving"
    expectations = {
        "sagemaker.notebooks": "nb",
        "athena.scans": "primary",
        "glue.jobs": "Flex",
        "emr.clusters": "spark",
        "bedrock.throughput": "pt",
        "bedrock.knowledge_bases": "docs",
        "messaging.queues": "jobs",
        "stepfunctions.type": "orders",
        "streaming.shards": "events-dev",
    }
    for check, text in expectations.items():
        assert checks[check].status == "manual" and text in checks[check].reason, check
    assert checks["quicksight.users"].status == "fine"  # not used
