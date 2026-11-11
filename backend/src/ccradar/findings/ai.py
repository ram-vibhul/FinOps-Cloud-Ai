"""Token waste in AI usage reported through the SDK (7.5), as savings findings."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from ccradar.costs.ai_insights import waste
from ccradar.findings.aws_compute import _usd
from ccradar.findings.registry import FindingIn, detector

STEPS = {
    "repeated_prompts": [
        "Mark the repeated part of the prompt (system prompt, instructions, documents) as cacheable.",
        "Keep that part identical between calls; put changing details after it.",
        "Check the provider's cache-read token counts go up in the next week.",
    ],
    "oversized_context": [
        "Send only the parts of the context the task needs (retrieve, summarise or truncate).",
        "Cache the stable part of the context if it repeats.",
    ],
    "expensive_model": [
        "Run a sample of these requests on the cheaper model and compare the answers.",
        "Switch when the quality holds; keep the bigger model for the hard cases.",
    ],
    "retries": [
        "Look at why calls are retried: timeouts, rate limits or answers cut off by max tokens.",
        "Raise limits or add back-off so the same work is not paid for twice.",
    ],
}
RISK = {
    "repeated_prompts": "low",
    "oversized_context": "medium",
    "expensive_model": "medium",
    "retries": "low",
}


@detector(
    "ai.token_waste",
    category="ai",
    title="Wasted AI tokens (repeats, oversized context, model choice, retries)",
)
def token_waste(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    items = waste(db, org_id)
    if not items:
        return []
    rate = 1.0 if currency == "USD" else _usd(db, org_id, currency)
    if rate is None:
        return []
    return [
        FindingIn(
            key=f"{w.kind}:{w.key}",
            title=w.title,
            description=w.detail,
            savings_monthly=round(w.saving_monthly * rate, 2),
            currency=currency,
            risk=RISK[w.kind],  # type: ignore[arg-type]
            effort="low" if w.kind in ("repeated_prompts", "retries") else "medium",
            provider="ai",
            service="AI usage",
            resource_type="ai_workload",
            resource_id=w.key,
            overlap=f"ai:{w.key}:{w.kind}",
            steps=STEPS[w.kind],
            evidence={"kind": w.kind, "requests": w.requests, "saving_usd": w.saving_monthly},
        )
        for w in items
    ]
