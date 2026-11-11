"""Analytics, AI/ML and messaging inventory (read-only List/Describe/Get calls):
Athena workgroups, Glue jobs and crawlers, EMR clusters, SageMaker endpoints and
notebooks, Bedrock provisioned throughput and knowledge bases, SQS queues, Step
Functions, Kinesis streams, MSK clusters and Amazon MQ brokers."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ccradar.inventory.aws import Found, _pages

MAX_CHILDREN = 500


def athena_workgroups(client: Any, region: str) -> Iterator[Found]:
    for page in _pages(client.list_work_groups, "NextToken", "NextToken", MaxResults=50):
        for w in page.get("WorkGroups", [])[:MAX_CHILDREN]:
            cfg = (client.get_work_group(WorkGroup=w["Name"]).get("WorkGroup") or {}).get(
                "Configuration"
            ) or {}
            yield Found(
                "Amazon Athena",
                "athena_workgroup",
                f"athena:{region}:{w['Name']}",
                region,
                name=w["Name"],
                state=str(w.get("State") or "").lower() or None,
                attributes={
                    "scan_limit_bytes": cfg.get("BytesScannedCutoffPerQuery"),
                    "enforced": bool(cfg.get("EnforceWorkGroupConfiguration")),
                },
            )


def glue_jobs(client: Any, region: str) -> Iterator[Found]:
    for page in _pages(client.get_jobs, "NextToken", "NextToken", MaxResults=200):
        for j in page.get("Jobs", [])[:MAX_CHILDREN]:
            yield Found(
                "AWS Glue",
                "glue_job",
                f"glue:{region}:job/{j['Name']}",
                region,
                name=j["Name"],
                state="active",
                created_at=j.get("CreatedOn"),
                attributes={
                    "command": (j.get("Command") or {}).get(
                        "Name"
                    ),  # glueetl | gluestreaming | pythonshell
                    "glue_version": j.get("GlueVersion"),
                    "worker_type": j.get("WorkerType"),
                    "workers": j.get("NumberOfWorkers"),
                    "execution_class": j.get("ExecutionClass") or "STANDARD",
                },
            )
    for page in _pages(client.get_crawlers, "NextToken", "NextToken", MaxResults=200):
        for c in page.get("Crawlers", [])[:MAX_CHILDREN]:
            yield Found(
                "AWS Glue",
                "glue_crawler",
                f"glue:{region}:crawler/{c['Name']}",
                region,
                name=c["Name"],
                state=str(c.get("State") or "").lower() or None,
                attributes={"schedule": (c.get("Schedule") or {}).get("ScheduleExpression")},
            )


def emr_clusters(client: Any, region: str) -> Iterator[Found]:
    for page in _pages(
        client.list_clusters,
        "Marker",
        "Marker",
        ClusterStates=["STARTING", "BOOTSTRAPPING", "RUNNING", "WAITING"],
    ):
        for c in page.get("Clusters", [])[:MAX_CHILDREN]:
            d = client.describe_cluster(ClusterId=c["Id"]).get("Cluster") or {}
            status = d.get("Status") or c.get("Status") or {}
            yield Found(
                "Amazon Elastic MapReduce",
                "emr_cluster",
                d.get("ClusterArn") or c["Id"],
                region,
                name=c.get("Name"),
                state=str(status.get("State") or "").lower() or None,
                created_at=(status.get("Timeline") or {}).get("CreationDateTime"),
                tags={t["Key"]: t.get("Value", "") for t in d.get("Tags") or [] if t.get("Key")},
                attributes={
                    "cluster_id": c["Id"],
                    "auto_terminate": bool(d.get("AutoTerminate")),
                    "instance_collection": d.get("InstanceCollectionType"),
                    "release": d.get("ReleaseLabel"),
                },
            )


def sagemaker_resources(client: Any, region: str) -> Iterator[Found]:
    for page in _pages(client.list_endpoints, "NextToken", "NextToken", MaxResults=100):
        for e in page.get("Endpoints", [])[:MAX_CHILDREN]:
            d = client.describe_endpoint(EndpointName=e["EndpointName"])
            variants = d.get("ProductionVariants") or []
            yield Found(
                "Amazon SageMaker",
                "sagemaker_endpoint",
                e["EndpointArn"],
                region,
                name=e["EndpointName"],
                state=e.get("EndpointStatus"),  # InService ...
                created_at=e.get("CreationTime"),
                attributes={
                    "variants": [v.get("VariantName") for v in variants],
                    "instances": sum(int(v.get("CurrentInstanceCount") or 0) for v in variants),
                    "serverless": any(v.get("CurrentServerlessConfig") for v in variants),
                },
            )
    for page in _pages(client.list_notebook_instances, "NextToken", "NextToken", MaxResults=100):
        for n in page.get("NotebookInstances", [])[:MAX_CHILDREN]:
            yield Found(
                "Amazon SageMaker",
                "sagemaker_notebook",
                n["NotebookInstanceArn"],
                region,
                name=n["NotebookInstanceName"],
                state=n.get("NotebookInstanceStatus"),
                created_at=n.get("CreationTime"),
                attributes={
                    "instance_type": n.get("InstanceType"),
                    "lifecycle_config": n.get("NotebookInstanceLifecycleConfigName"),
                },
            )


def bedrock_throughput(client: Any, region: str) -> Iterator[Found]:
    for page in _pages(
        client.list_provisioned_model_throughputs, "nextToken", "nextToken", maxResults=100
    ):
        for p in page.get("provisionedModelSummaries", [])[:MAX_CHILDREN]:
            yield Found(
                "Amazon Bedrock",
                "bedrock_provisioned_throughput",
                p["provisionedModelArn"],
                region,
                name=p.get("provisionedModelName"),
                state=str(p.get("status") or "").lower() or None,
                created_at=p.get("creationTime"),
                attributes={
                    "model": p.get("foundationModelArn") or p.get("modelArn"),
                    "model_units": p.get("modelUnits"),
                    "commitment": p.get("commitmentDuration"),  # None = no commitment (hourly)
                },
            )


def bedrock_knowledge_bases(client: Any, region: str) -> Iterator[Found]:
    for page in _pages(client.list_knowledge_bases, "nextToken", "nextToken", maxResults=100):
        for kb in page.get("knowledgeBaseSummaries", [])[:MAX_CHILDREN]:
            d = (
                client.get_knowledge_base(knowledgeBaseId=kb["knowledgeBaseId"]).get(
                    "knowledgeBase"
                )
                or {}
            )
            yield Found(
                "Amazon Bedrock",
                "bedrock_knowledge_base",
                d.get("knowledgeBaseArn") or kb["knowledgeBaseId"],
                region,
                name=kb.get("name"),
                state=str(kb.get("status") or "").lower() or None,
                attributes={"vector_store": (d.get("storageConfiguration") or {}).get("type")},
            )


def sqs_queues(client: Any, region: str) -> Iterator[Found]:
    for page in _pages(client.list_queues, "NextToken", "NextToken", MaxResults=1000):
        for url in page.get("QueueUrls", [])[:MAX_CHILDREN]:
            a = client.get_queue_attributes(
                QueueUrl=url, AttributeNames=["ReceiveMessageWaitTimeSeconds", "QueueArn"]
            ).get("Attributes", {})
            yield Found(
                "Amazon Simple Queue Service",
                "sqs_queue",
                a.get("QueueArn") or url,
                region,
                name=url.rsplit("/", 1)[-1],
                state="active",
                attributes={"wait_seconds": int(a.get("ReceiveMessageWaitTimeSeconds") or 0)},
            )


def state_machines(client: Any, region: str) -> Iterator[Found]:
    for page in _pages(client.list_state_machines, "nextToken", "nextToken", maxResults=100):
        for m in page.get("stateMachines", [])[:MAX_CHILDREN]:
            yield Found(
                "AWS Step Functions",
                "state_machine",
                m["stateMachineArn"],
                region,
                name=m.get("name"),
                state="active",
                created_at=m.get("creationDate"),
                attributes={"type": m.get("type")},  # STANDARD | EXPRESS
            )


def kinesis_streams(client: Any, region: str) -> Iterator[Found]:
    names = [
        n
        for page in _pages(client.list_streams, "NextToken", "NextToken", Limit=100)
        for n in page.get("StreamNames", [])
    ]
    for name in names[:MAX_CHILDREN]:
        s = client.describe_stream_summary(StreamName=name).get("StreamDescriptionSummary") or {}
        yield Found(
            "Amazon Kinesis",
            "kinesis_stream",
            s.get("StreamARN") or name,
            region,
            name=name,
            state=str(s.get("StreamStatus") or "").lower() or None,
            attributes={
                "mode": (s.get("StreamModeDetails") or {}).get("StreamMode") or "PROVISIONED",
                "shards": s.get("OpenShardCount"),
                "retention_hours": s.get("RetentionPeriodHours"),
            },
        )


def msk_clusters(client: Any, region: str) -> Iterator[Found]:
    for page in _pages(client.list_clusters_v2, "NextToken", "NextToken", MaxResults=100):
        for c in page.get("ClusterInfoList", [])[:MAX_CHILDREN]:
            prov = c.get("Provisioned") or {}
            yield Found(
                "Amazon Managed Streaming for Apache Kafka",
                "msk_cluster",
                c["ClusterArn"],
                region,
                name=c.get("ClusterName"),
                state=str(c.get("State") or "").lower() or None,
                created_at=c.get("CreationTime"),
                tags=c.get("Tags") or {},
                attributes={
                    "type": c.get("ClusterType"),  # PROVISIONED | SERVERLESS
                    "brokers": prov.get("NumberOfBrokerNodes"),
                    "broker_type": (prov.get("BrokerNodeGroupInfo") or {}).get("InstanceType"),
                },
            )


def mq_brokers(client: Any, region: str) -> Iterator[Found]:
    for page in _pages(client.list_brokers, "NextToken", "NextToken", MaxResults=100):
        for b in page.get("BrokerSummaries", [])[:MAX_CHILDREN]:
            yield Found(
                "Amazon MQ",
                "mq_broker",
                b["BrokerArn"],
                region,
                name=b.get("BrokerName"),
                state=str(b.get("BrokerState") or "").lower() or None,
                created_at=b.get("Created"),
                attributes={
                    "deployment": b.get(
                        "DeploymentMode"
                    ),  # SINGLE_INSTANCE | ACTIVE_STANDBY_MULTI_AZ | CLUSTER_MULTI_AZ
                    "instance_type": b.get("HostInstanceType"),
                    "engine": b.get("EngineType"),
                },
            )


ANALYTICS_COLLECTORS = [
    ("athena", athena_workgroups),
    ("glue", glue_jobs),
    ("emr", emr_clusters),
    ("sagemaker", sagemaker_resources),
    ("bedrock", bedrock_throughput),
    ("bedrock-agent", bedrock_knowledge_bases),
    ("sqs", sqs_queues),
    ("stepfunctions", state_machines),
    ("kinesis", kinesis_streams),
    ("kafka", msk_clusters),
    ("mq", mq_brokers),
]
ANALYTICS_TYPES = {
    "athena_workgroups": ("athena_workgroup",),
    "glue_jobs": ("glue_job", "glue_crawler"),
    "emr_clusters": ("emr_cluster",),
    "sagemaker_resources": ("sagemaker_endpoint", "sagemaker_notebook"),
    "bedrock_throughput": ("bedrock_provisioned_throughput",),
    "bedrock_knowledge_bases": ("bedrock_knowledge_base",),
    "sqs_queues": ("sqs_queue",),
    "state_machines": ("state_machine",),
    "kinesis_streams": ("kinesis_stream",),
    "msk_clusters": ("msk_cluster",),
    "mq_brokers": ("mq_broker",),
}
