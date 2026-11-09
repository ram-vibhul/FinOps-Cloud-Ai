"""Compute and container inventory: Lambda, Auto Scaling, ECS, Batch, ECR and EKS
(read-only ``List*``/``Describe*``/``Get*`` calls; no data-plane access)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

from costlens.inventory.aws import MAX_PAGES, Found, _pages, _tags

ECR_KEEP_NEWEST = 10  # images per repository we never count as stale
ECR_STALE_DAYS = 180
MAX_CHILDREN = 100  # services per ECS cluster, node groups per EKS cluster, ...


def _code(error: Exception) -> str:
    return str((getattr(error, "response", None) or {}).get("Error", {}).get("Code", ""))


def lambda_functions(client: Any, region: str) -> Iterator[Found]:
    for page in _pages(client.list_functions, "Marker", "NextMarker", MaxItems=50):
        for f in page.get("Functions", []):
            yield Found(
                "AWS Lambda",
                "lambda_function",
                f["FunctionArn"],
                region,
                name=f.get("FunctionName"),
                state=f.get("State") or "Active",
                attributes={
                    "memory_mb": f.get("MemorySize"),
                    "timeout_s": f.get("Timeout"),
                    "runtime": f.get("Runtime"),
                    "architectures": f.get("Architectures") or ["x86_64"],
                    "package_type": f.get("PackageType") or "Zip",
                    "last_modified": f.get("LastModified"),
                },
            )


def autoscaling_groups(client: Any, region: str) -> Iterator[Found]:
    for page in _pages(
        client.describe_auto_scaling_groups, "NextToken", "NextToken", MaxRecords=100
    ):
        for g in page.get("AutoScalingGroups", []):
            tags = _tags(g.get("Tags"))
            mixed = g.get("MixedInstancesPolicy") or {}
            dist = mixed.get("InstancesDistribution") or {}
            on_demand_pct = dist.get(
                "OnDemandPercentageAboveBaseCapacity", 100 if not mixed else None
            )
            instances = g.get("Instances") or []
            yield Found(
                "Amazon EC2 Auto Scaling",
                "autoscaling_group",
                g["AutoScalingGroupARN"],
                region,
                name=g.get("AutoScalingGroupName"),
                state="active" if not g.get("Status") else str(g["Status"]).lower(),
                created_at=g.get("CreatedTime"),
                tags=tags,
                attributes={
                    "min": g.get("MinSize"),
                    "max": g.get("MaxSize"),
                    "desired": g.get("DesiredCapacity"),
                    "instance_ids": [i["InstanceId"] for i in instances if i.get("InstanceId")],
                    "instance_types": sorted(
                        {i["InstanceType"] for i in instances if i.get("InstanceType")}
                    ),
                    "on_demand_pct": 100 if on_demand_pct is None else on_demand_pct,
                    "eks_cluster": tags.get("eks:cluster-name"),
                    "eks_nodegroup": tags.get("eks:nodegroup-name"),
                },
            )


def ecs_services(client: Any, region: str) -> Iterator[Found]:
    clusters = [
        arn
        for page in _pages(client.list_clusters, "nextToken", "nextToken", maxResults=100)
        for arn in page.get("clusterArns", [])
    ]
    for cluster in clusters[:MAX_CHILDREN]:
        cluster_name = cluster.rsplit("/", 1)[-1]
        arns = [
            arn
            for page in _pages(
                client.list_services, "nextToken", "nextToken", cluster=cluster, maxResults=100
            )
            for arn in page.get("serviceArns", [])
        ][:MAX_CHILDREN]
        for i in range(0, len(arns), 10):  # DescribeServices takes 10 at a time
            for s in client.describe_services(
                cluster=cluster, services=arns[i : i + 10], include=["TAGS"]
            ).get("services", []):
                strategy = [
                    p.get("capacityProvider") for p in s.get("capacityProviderStrategy") or []
                ]
                yield Found(
                    "Amazon Elastic Container Service",
                    "ecs_service",
                    s["serviceArn"],
                    region,
                    name=s.get("serviceName"),
                    state=str(s.get("status") or "").lower() or None,
                    created_at=s.get("createdAt"),
                    tags={
                        t["key"]: t.get("value", "") for t in s.get("tags") or [] if t.get("key")
                    },
                    attributes={
                        "cluster": cluster_name,
                        "launch_type": s.get("launchType")
                        or (
                            "FARGATE"
                            if any(p and p.startswith("FARGATE") for p in strategy)
                            else None
                        ),
                        "capacity_providers": strategy,
                        "desired": s.get("desiredCount"),
                        "running": s.get("runningCount"),
                    },
                )


def batch_environments(client: Any, region: str) -> Iterator[Found]:
    for page in _pages(client.describe_compute_environments, "nextToken", "nextToken"):
        for e in page.get("computeEnvironments", []):
            res = e.get("computeResources") or {}
            yield Found(
                "AWS Batch",
                "batch_compute_environment",
                e["computeEnvironmentArn"],
                region,
                name=e.get("computeEnvironmentName"),
                state=str(e.get("state") or "").lower() or None,
                tags=e.get("tags") or {},
                attributes={
                    "managed": e.get("type") == "MANAGED",
                    "compute_type": res.get("type"),  # EC2 | SPOT | FARGATE | FARGATE_SPOT
                    "min_vcpus": res.get("minvCpus"),
                    "desired_vcpus": res.get("desiredvCpus"),
                    "max_vcpus": res.get("maxvCpus"),
                    "instance_types": res.get("instanceTypes") or [],
                },
            )


def _ecr_images(client: Any, repo: str, now: datetime) -> dict[str, Any]:
    images: list[tuple[datetime, int, bool]] = []
    for page in _pages(
        client.describe_images, "nextToken", "nextToken", repositoryName=repo, maxResults=1000
    ):
        for img in page.get("imageDetails", []):
            pushed = img.get("imagePushedAt") or now
            pushed = pushed if pushed.tzinfo else pushed.replace(tzinfo=UTC)
            images.append(
                (pushed, int(img.get("imageSizeInBytes") or 0), bool(img.get("imageTags")))
            )
    images.sort(key=lambda x: x[0], reverse=True)
    tagged = [i for i in images if i[2]]
    keep = {id(i) for i in tagged[:ECR_KEEP_NEWEST]}
    old = now - timedelta(days=ECR_STALE_DAYS)
    return {
        "image_count": len(images),
        "total_bytes": sum(i[1] for i in images),
        "untagged_bytes": sum(i[1] for i in images if not i[2]),
        "stale_bytes": sum(i[1] for i in tagged if id(i) not in keep and i[0] < old),
        "truncated": len(images) >= 1000 * MAX_PAGES,
    }


def ecr_repositories(client: Any, region: str, now: datetime | None = None) -> Iterator[Found]:
    now = now or datetime.now(UTC)
    for page in _pages(client.describe_repositories, "nextToken", "nextToken", maxResults=100):
        for r in page.get("repositories", []):
            name = r["repositoryName"]
            try:
                client.get_lifecycle_policy(repositoryName=name)
                lifecycle: bool | None = True
            except Exception as error:
                lifecycle = False if _code(error) == "LifecyclePolicyNotFoundException" else None
            yield Found(
                "Amazon EC2 Container Registry (ECR)",
                "ecr_repository",
                r["repositoryArn"],
                region,
                name=name,
                state="active",
                created_at=r.get("createdAt"),
                attributes={"lifecycle_policy": lifecycle, **_ecr_images(client, name, now)},
            )


def eks_clusters(client: Any, region: str) -> Iterator[Found]:
    names = [
        n
        for page in _pages(client.list_clusters, "nextToken", "nextToken", maxResults=100)
        for n in page.get("clusters", [])
    ]
    for name in names[:MAX_CHILDREN]:
        c = client.describe_cluster(name=name).get("cluster") or {}
        tags = c.get("tags") or {}
        yield Found(
            "Amazon Elastic Kubernetes Service",
            "eks_cluster",
            c.get("arn") or name,
            region,
            name=name,
            state=str(c.get("status") or "").lower() or None,
            created_at=c.get("createdAt"),
            tags=tags,
            attributes={
                "version": c.get("version"),
                "support_type": (c.get("upgradePolicy") or {}).get("supportType"),
            },
        )
        groups = [
            g
            for page in _pages(
                client.list_nodegroups, "nextToken", "nextToken", clusterName=name, maxResults=100
            )
            for g in page.get("nodegroups", [])
        ]
        for g in groups[:MAX_CHILDREN]:
            ng = client.describe_nodegroup(clusterName=name, nodegroupName=g).get("nodegroup") or {}
            scaling = ng.get("scalingConfig") or {}
            yield Found(
                "Amazon Elastic Kubernetes Service",
                "eks_nodegroup",
                ng.get("nodegroupArn") or f"{name}/{g}",
                region,
                name=g,
                state=str(ng.get("status") or "").lower() or None,
                created_at=ng.get("createdAt"),
                tags=ng.get("tags") or {},
                attributes={
                    "cluster": name,
                    "capacity_type": ng.get("capacityType") or "ON_DEMAND",
                    "instance_types": ng.get("instanceTypes") or [],
                    "min": scaling.get("minSize"),
                    "max": scaling.get("maxSize"),
                    "desired": scaling.get("desiredSize"),
                    "version": ng.get("version"),
                },
            )


COMPUTE_COLLECTORS = [
    ("lambda", lambda_functions),
    ("autoscaling", autoscaling_groups),
    ("ecs", ecs_services),
    ("batch", batch_environments),
    ("ecr", ecr_repositories),
    ("eks", eks_clusters),
]
