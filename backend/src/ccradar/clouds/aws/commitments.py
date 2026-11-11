"""Savings Plans and Reserved Instances from Cost Explorer (read-only; each call costs
USD 0.01, about 8 calls per connection per day).

Normalised into one dict per snapshot:
  savings_plans: utilisation %, used/unused commitment, net savings, coverage %, on-demand spend
  reservations: utilisation %, unused hours cost, coverage %
  sp_recommendations: [{plan_type, hourly_commitment, monthly_savings, savings_pct, ...}]
  ri_recommendations: [{service, instance_type, count, monthly_savings, upfront, ...}]"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

log = logging.getLogger(__name__)
RI_SERVICES = (
    "Amazon Relational Database Service",
    "Amazon ElastiCache",
    "Amazon OpenSearch Service",
    "Amazon Redshift",
)


def _f(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _safe(name: str, call: Any, errors: list[str]) -> Any:
    try:
        return call()
    except Exception as error:  # one missing piece (e.g. no reservations) must not stop the rest
        errors.append(f"{name}: {type(error).__name__}")
        log.info("Cost Explorer %s unavailable: %s", name, type(error).__name__)
        return None


def collect(ce: Any, today: date) -> dict[str, Any]:
    period = {"Start": (today - timedelta(days=30)).isoformat(), "End": today.isoformat()}
    errors: list[str] = []
    out: dict[str, Any] = {"period": period, "errors": errors}

    util = _safe(
        "savings_plans_utilization",
        lambda: ce.get_savings_plans_utilization(TimePeriod=period),
        errors,
    )
    cov = _safe(
        "savings_plans_coverage",
        lambda: ce.get_savings_plans_coverage(TimePeriod=period, Granularity="MONTHLY"),
        errors,
    )
    if util is not None or cov is not None:
        total = (util or {}).get("Total") or {}
        u, s = total.get("Utilization") or {}, total.get("Savings") or {}
        covered = on_demand = 0.0
        for c in (cov or {}).get("SavingsPlansCoverages") or []:
            co = c.get("Coverage") or {}
            covered += _f(co.get("SpendCoveredBySavingsPlans"))
            on_demand += _f(co.get("OnDemandCost"))
        out["savings_plans"] = {
            "utilization_pct": _f(u.get("UtilizationPercentage")),
            "total_commitment": _f(u.get("TotalCommitment")),
            "used_commitment": _f(u.get("UsedCommitment")),
            "unused_commitment": _f(u.get("UnusedCommitment")),
            "net_savings": _f(s.get("NetSavings")),
            "covered_spend": covered,
            "on_demand_spend": on_demand,
            "coverage_pct": round(100 * covered / (covered + on_demand), 1)
            if covered + on_demand
            else None,
        }

    ri_util = _safe(
        "reservation_utilization", lambda: ce.get_reservation_utilization(TimePeriod=period), errors
    )
    ri_cov = _safe(
        "reservation_coverage", lambda: ce.get_reservation_coverage(TimePeriod=period), errors
    )
    if ri_util is not None or ri_cov is not None:
        t = (ri_util or {}).get("Total") or {}
        c = (((ri_cov or {}).get("Total") or {}).get("CoverageHours")) or {}
        out["reservations"] = {
            "utilization_pct": _f(t.get("UtilizationPercentage")),
            "unused_hours": _f(t.get("UnusedHours")),
            "net_savings": _f(t.get("NetRISavings")),
            "coverage_pct": _f(c.get("CoverageHoursPercentage")) if c else None,
        }

    recs = []
    for plan in ("COMPUTE_SP", "EC2_INSTANCE_SP"):
        r = _safe(
            f"sp_recommendation_{plan}",
            lambda plan=plan: ce.get_savings_plans_purchase_recommendation(
                SavingsPlansType=plan,
                TermInYears="ONE_YEAR",
                PaymentOption="NO_UPFRONT",
                LookbackPeriodInDays="THIRTY_DAYS",
            ),
            errors,
        )
        summary = ((r or {}).get("SavingsPlansPurchaseRecommendation") or {}).get(
            "SavingsPlansPurchaseRecommendationSummary"
        ) or {}
        if _f(summary.get("EstimatedMonthlySavingsAmount")) > 0:
            recs.append(
                {
                    "plan_type": plan,
                    "term": "1 year",
                    "payment": "no upfront",
                    "hourly_commitment": _f(summary.get("HourlyCommitmentToPurchase")),
                    "monthly_savings": _f(summary.get("EstimatedMonthlySavingsAmount")),
                    "savings_pct": _f(summary.get("EstimatedSavingsPercentage")),
                    "on_demand_monthly": _f(summary.get("CurrentOnDemandSpend")),
                    "currency": summary.get("CurrencyCode") or "USD",
                }
            )
    out["sp_recommendations"] = recs

    ri_recs = []
    for service in RI_SERVICES:
        r = _safe(
            f"ri_recommendation_{service}",
            lambda service=service: ce.get_reservation_purchase_recommendation(
                Service=service,
                TermInYears="ONE_YEAR",
                PaymentOption="NO_UPFRONT",
                LookbackPeriodInDays="THIRTY_DAYS",
            ),
            errors,
        )
        for rec in (r or {}).get("Recommendations") or []:
            for d in rec.get("RecommendationDetails") or []:
                details = d.get("InstanceDetails") or {}
                inner = next(iter(details.values()), {}) if details else {}
                saving = _f(d.get("EstimatedMonthlySavingsAmount"))
                if saving <= 0:
                    continue
                ri_recs.append(
                    {
                        "service": service,
                        "term": "1 year",
                        "payment": "no upfront",
                        "instance_type": inner.get("InstanceType") or inner.get("NodeType") or "",
                        "region": inner.get("Region"),
                        "engine": inner.get("DatabaseEngine"),
                        "count": _f(d.get("RecommendedNumberOfInstancesToPurchase")),
                        "monthly_savings": saving,
                        "savings_pct": _f(d.get("EstimatedMonthlySavingsPercentage")),
                        "upfront": _f(d.get("UpfrontCost")),
                        "currency": d.get("CurrencyCode") or "USD",
                    }
                )
    out["ri_recommendations"] = ri_recs
    return out
