"""Executive summary: the few numbers leadership asks about, from what CloudCostRadar already
knows: spend and forecast, budgets at risk, savings found and kept, commitments,
unit costs, policy violations and anomalies."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ccradar.costs import analytics
from ccradar.db.base import utcnow
from ccradar.models import CostAnomaly, Finding, Organization, PolicyViolation, UnitMetric


def summary(db: Session, org_id: uuid.UUID) -> dict[str, Any]:
    org = db.get(Organization, org_id)
    currency = org.default_currency if org else "USD"
    today = utcnow().date()
    spend = analytics.summary(db, org_id, currency, "effective", today)
    end = analytics.latest_date(db, org_id) or today
    movers = analytics.movers(db, org_id, currency, "effective", "service", end, 30, 5)

    from ccradar.costs.budgets import all_status

    budgets = all_status(db, org_id, today)
    at_risk = [b for b in budgets if (b["forecast_pct"] or 0) >= 100]

    from ccradar.findings.kept import summary as kept_summary
    from ccradar.findings.plan import build as plan
    from ccradar.findings.service import _per_resource

    open_findings = list(
        db.scalars(select(Finding).where(Finding.status.in_(("open", "approved"))))
    )
    quick = plan(db)
    kept = kept_summary(db)

    from ccradar.findings.commitments import latest

    snaps = latest(db)
    sp = (snaps[0][0].data.get("savings_plans") or {}) if snaps else {}

    unit = None
    first = db.scalar(select(UnitMetric).order_by(UnitMetric.created_at))
    if first is not None:
        from datetime import timedelta

        from ccradar.costs.unit_economics import report

        r = report(db, org_id, first, today - timedelta(days=89), today)
        unit = {
            "name": r["metric"]["name"],
            "unit": r["metric"]["unit"],
            "per_unit": r["per_unit"],
            "change_pct": r["change_pct"],
        }

    violations = (
        db.scalar(
            select(func.count())
            .select_from(PolicyViolation)
            .where(PolicyViolation.status == "open")
        )
        or 0
    )
    anomalies = (
        db.scalar(
            select(func.count())
            .select_from(CostAnomaly)
            .where(CostAnomaly.last_day >= today.replace(day=1))
        )
        or 0
    )
    return {
        "currency": currency,
        "as_of": spend.get("data_through"),
        "spend": {
            "month_to_date": spend["month_to_date"],
            "forecast": spend["forecast"]["amount"],
            "last_month": spend["last_month"],
            "change_vs_last_month_pct": spend["forecast"]["change_vs_last_month_pct"],
        },
        "movers": movers.get("items", [])[:5],
        "budgets": {
            "count": len(budgets),
            "at_risk": [
                {"name": b["name"], "forecast_pct": b["forecast_pct"]} for b in at_risk[:5]
            ],
        },
        "savings": {
            "open_monthly": round(sum(_per_resource(open_findings).values()), 2),
            "open_count": len(open_findings),
            "this_week_monthly": next(
                (b["savings_monthly"] for b in quick["buckets"] if b["key"] == "this_week"), 0.0
            ),
            "kept_latest_month": kept["kept_latest_month"],
            "kept_month": kept["latest_month"],
        },
        "commitments": {
            "coverage_pct": sp.get("coverage_pct"),
            "utilization_pct": sp.get("utilization_pct"),
        },
        "unit_cost": unit,
        "policy_violations": violations,
        "anomalies_this_month": anomalies,
    }
