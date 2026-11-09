"""Import AWS's own cost advice (read-only) and normalise it to resource + action.

- Cost Optimization Hub (``ListRecommendations``): the broadest source; it already folds
  in much of Compute Optimizer.
- Compute Optimizer: EC2 instances, EBS volumes and Lambda functions, per region.
- Trusted Advisor (new API): cost-optimising recommendations and their resources.

A source that is not enabled (not enrolled, no Business support) is recorded and
skipped; the rest still import."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete
from sqlalchemy.orm import Session

from costlens.clouds.aws.client import AwsAccessError, AwsGateway
from costlens.db.base import utcnow
from costlens.db.tenant import set_tenant
from costlens.inventory.aws import _pages
from costlens.inventory.aws_compute import _code
from costlens.models import CloudConnection, ExternalRecommendation

SOURCES = {
    "cost_optimization_hub": "AWS Cost Optimization Hub",
    "compute_optimizer": "AWS Compute Optimizer",
    "trusted_advisor": "AWS Trusted Advisor",
    "azure_advisor": "Azure Advisor",
    "gcp_recommender": "Google Cloud Recommender",
}
PROVIDER = {"azure_advisor": "azure", "gcp_recommender": "gcp"}  # everything else is AWS
CLOUD_NAMES = {"aws": "AWS", "azure": "Azure", "gcp": "Google Cloud"}
COH_ACTIONS = {
    "Rightsize": "rightsize",
    "Stop": "stop",
    "Delete": "delete",
    "ScaleIn": "rightsize",
    "Upgrade": "upgrade",
    "MigrateToGraviton": "graviton",
    "PurchaseSavingsPlans": "purchase_savings_plan",
    "PurchaseReservedInstances": "purchase_reservation",
}
TA_ACTIONS = {  # check name keywords -> action
    "low utilization": "stop",
    "idle": "delete",
    "unassociated elastic ip": "delete",
    "underutilized": "rightsize",
    "savings plan": "purchase_savings_plan",
    "reserved": "purchase_reservation",
}
MAX_ITEMS = 2000


@dataclass
class Advice:
    source: str
    source_id: str
    action: str
    title: str
    savings_monthly: float
    currency: str = "USD"
    account_id: str | None = None
    region: str | None = None
    resource_id: str | None = None
    resource_type: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


def _float(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _money(text: Any) -> float:
    return _float(str(text or "0").replace("$", "").replace(",", ""))


def cost_optimization_hub(client: Any) -> Iterator[Advice]:
    for i, page in enumerate(
        _pages(client.list_recommendations, "nextToken", "nextToken", maxResults=1000)
    ):
        for r in page.get("items", []):
            if i * 1000 > MAX_ITEMS:
                return
            action = COH_ACTIONS.get(str(r.get("actionType")), "other")
            yield Advice(
                "cost_optimization_hub",
                str(r.get("recommendationId")),
                action,
                f"{r.get('actionType')} {r.get('currentResourceType') or ''} "
                f"{r.get('currentResourceSummary') or ''}".strip(),
                _float(r.get("estimatedMonthlySavings")),
                str(r.get("currencyCode") or "USD"),
                r.get("accountId"),
                r.get("region"),
                r.get("resourceId") or r.get("resourceArn"),
                r.get("currentResourceType"),
                {
                    "arn": r.get("resourceArn"),
                    "recommended": r.get("recommendedResourceSummary"),
                    "effort": r.get("implementationEffort"),
                    "restart": r.get("restartNeeded"),
                    "rollback": r.get("rollbackPossible"),
                    "savings_pct": r.get("estimatedSavingsPercentage"),
                },
            )


def _best_option(options: list[dict[str, Any]]) -> tuple[dict[str, Any], float, str]:
    def saving(o: dict[str, Any]) -> tuple[float, str]:
        s = (o.get("savingsOpportunity") or {}).get("estimatedMonthlySavings") or {}
        return _float(s.get("value")), str(s.get("currency") or "USD")

    best = max(options, key=lambda o: saving(o)[0], default={})
    value, currency = saving(best) if best else (0.0, "USD")
    return best, value, currency


def _resource_id(arn: str, label: str) -> str:
    """The id our inventory uses: instance/volume id, or the Lambda ARN without version."""
    if label == "Lambda function":
        return arn.rsplit(":", 1)[0] if arn.count(":") == 7 else arn
    return arn.rsplit("/", 1)[-1]


def compute_optimizer(client: Any, region: str) -> Iterator[Advice]:
    calls = (
        ("get_ec2_instance_recommendations", "instanceRecommendations", "instanceArn",
         "EC2 instance", "recommendationOptions", "instanceType"),
        ("get_ebs_volume_recommendations", "volumeRecommendations", "volumeArn", "EBS volume",
         "volumeRecommendationOptions", "configuration"),
        ("get_lambda_function_recommendations", "lambdaFunctionRecommendations", "functionArn",
         "Lambda function", "memorySizeRecommendationOptions", "memorySize"),
    )  # fmt: skip
    for method, key, arn_key, label, options_key, what in calls:
        for page in _pages(getattr(client, method), "nextToken", "nextToken"):
            for r in page.get(key, [])[:MAX_ITEMS]:
                finding = str(r.get("finding") or "")
                if finding.lower() in ("optimized", "unavailable", ""):
                    continue
                best, value, currency = _best_option(r.get(options_key) or [])
                if value <= 0:
                    continue
                arn = str(r.get(arn_key) or "")
                rid = _resource_id(arn, label)
                yield Advice(
                    "compute_optimizer",
                    f"{method}:{arn}",
                    "rightsize",
                    f"{label} {finding.lower()}: recommended {best.get(what)}",
                    value,
                    currency,
                    r.get("accountId"),
                    region,
                    rid,
                    label,
                    {"arn": arn, "finding": finding, "recommended": best.get(what)},
                )


def trusted_advisor(client: Any) -> Iterator[Advice]:
    for page in _pages(
        client.list_recommendations, "nextToken", "nextToken", pillar="cost_optimizing"
    ):
        for rec in page.get("recommendationSummaries", []):
            if rec.get("status") == "ok":
                continue
            name = str(rec.get("name") or "")
            action = next((a for k, a in TA_ACTIONS.items() if k in name.lower()), "other")
            for rp in _pages(
                client.list_recommendation_resources,
                "nextToken",
                "nextToken",
                recommendationIdentifier=rec["arn"],
            ):
                for res in rp.get("recommendationResourceSummaries", [])[:MAX_ITEMS]:
                    meta = res.get("metadata") or {}
                    yield Advice(
                        "trusted_advisor",
                        str(res.get("arn") or f"{rec['arn']}:{res.get('awsResourceId')}"),
                        action,
                        name,
                        _float(
                            meta.get("Estimated Monthly Savings", "0")
                            .replace("$", "")
                            .replace(",", "")
                        )
                        if isinstance(meta.get("Estimated Monthly Savings"), str)
                        else 0.0,
                        "USD",
                        res.get("accountId"),
                        res.get("regionCode"),
                        res.get("awsResourceId"),
                        None,
                        {"check": name, "status": res.get("status")},
                    )


def collect(
    db: Session,
    gateway: AwsGateway,
    connection_id: uuid.UUID,
    regions: list[str] | None = None,
) -> dict[str, Any]:
    """Replace this connection's imported advice with a fresh copy. Returns counts/errors."""
    set_tenant(db, bypass=True)
    conn = db.get(CloudConnection, connection_id)
    if conn is None or conn.status != "active" or not conn.role_arn:
        return {}
    org_id = conn.org_id
    if regions is None:
        from costlens.inventory.service import billed_regions

        regions = billed_regions(db, org_id, conn.account_id)
    try:
        session = gateway.assume(conn.role_arn, conn.external_id, f"costlens-{org_id}")
    except AwsAccessError:
        return {"errors": {"assume": "access_denied"}}
    found: list[Advice] = []
    errors: dict[str, str] = {}

    def coh() -> list[Advice]:
        client = gateway.client(session, "cost-optimization-hub", region="us-east-1")
        return list(cost_optimization_hub(client))

    def ta() -> list[Advice]:
        return list(trusted_advisor(gateway.client(session, "trustedadvisor", region="us-east-1")))

    def co(region: str) -> list[Advice]:
        client = gateway.client(session, "compute-optimizer", region=region)
        return list(compute_optimizer(client, region))

    jobs: list[tuple[str, Any]] = [("cost_optimization_hub", coh), ("trusted_advisor", ta)]
    jobs += [(f"compute_optimizer:{r}", lambda r=r: co(r)) for r in regions]
    for name, job in jobs:
        try:
            found += job()
        except Exception as error:
            errors[name] = _code(error) or type(error).__name__
    set_tenant(db, org_id=org_id)
    now = utcnow()
    succeeded = {a.source for a in found} | {
        n.split(":")[0] for n, _ in jobs if not any(e.startswith(n.split(":")[0]) for e in errors)
    }
    db.execute(
        delete(ExternalRecommendation).where(
            ExternalRecommendation.connection_id == conn.id,
            ExternalRecommendation.source.in_(succeeded),
        )
    )
    seen: set[tuple[str, str]] = set()
    for a in found:
        if (a.source, a.source_id) in seen:
            continue
        seen.add((a.source, a.source_id))
        db.add(
            ExternalRecommendation(
                org_id=org_id,
                connection_id=conn.id,
                source=a.source,
                source_id=a.source_id[:500],
                account_id=a.account_id,
                region=a.region,
                resource_id=(a.resource_id or "")[:1000] or None,
                resource_type=a.resource_type,
                action=a.action,
                title=a.title[:500],
                savings_monthly=round(a.savings_monthly, 2),
                currency=a.currency[:3],
                data=a.data,
                fetched_at=now,
            )
        )
    conn.settings = {
        **(conn.settings or {}),
        "advice": {"fetched_at": now.isoformat(), "errors": errors, "count": len(seen)},
    }
    db.flush()
    return {"count": len(seen), "errors": errors}
