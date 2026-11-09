"""AI/ML detectors: SageMaker endpoints with no invocations for a week."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from costlens.findings.aws import MONTH, _common, _resources
from costlens.findings.aws_usage import MIN_DAYS, _usage
from costlens.findings.registry import FindingIn, detector
from costlens.inventory.service import resource_costs

MIN_SAVING = 1.0


@detector(
    "aws.sagemaker_idle_endpoints", category="waste", title="SageMaker endpoints nobody calls"
)
def sagemaker_idle_endpoints(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    endpoints = [
        e
        for e in _resources(db, "sagemaker_endpoint")
        if e.state == "InService" and not (e.attributes or {}).get("serverless")
    ]
    ids = [e.resource_id for e in endpoints]
    usage, cost = _usage(db, ids), resource_costs(db, org_id, ids, days=30, currency=currency)
    out = []
    for e in endpoints:
        calls = usage.get(e.resource_id, {}).get("invocations")
        monthly = cost.get(e.resource_id, 0.0) * MONTH / 30
        if not calls or calls["days"] < MIN_DAYS or calls["max"] > 0 or monthly < MIN_SAVING:
            continue
        instances = (e.attributes or {}).get("instances") or 0
        out.append(
            FindingIn(
                key=e.resource_id,
                title=f"Delete idle SageMaker endpoint {e.name}",
                description=(
                    f"No invocations in {calls['days']} days, yet its {instances} instance(s) "
                    "are billed every hour. Keep the endpoint configuration to recreate it in "
                    "minutes, or use Serverless Inference for occasional traffic."
                ),
                savings_monthly=round(monthly, 2),
                currency=currency,
                risk="medium",
                effort="low",
                overlap=e.resource_id,
                **_common(e),
                steps=[
                    "Confirm nothing calls it (batch jobs, other accounts).",
                    "Delete the endpoint; keep its configuration and model.",
                ],
                commands=[
                    f"aws sagemaker delete-endpoint --region {e.region} --endpoint-name {e.name}"
                ],
                evidence={
                    "days": calls["days"],
                    "instances": instances,
                    "monthly_cost": round(monthly, 2),
                },
            )
        )
    return out
