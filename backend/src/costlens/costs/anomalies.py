"""Cost anomaly detection with a seasonal, robust baseline.

For each series (total, every service, every account) and each recent day:

* expected = median of the previous 28 days x the day-of-week factor (median of
  that weekday / overall median), so quiet weekends are not "drops" and busy
  Mondays are not "spikes";
* spread = 1.4826 x median absolute deviation of the previous days' residuals
  (a robust standard deviation: one past spike cannot hide the next one);
* anomaly when actual > expected + k x spread **and** the extra spend is at least
  the minimum impact (absolute) and 10% of expected.

Only increases are flagged: they cost money. Sensitivity sets k (high 2.5,
medium 3, low 4). Detection is idempotent per (day, dimension, key, currency).
"""

from __future__ import annotations

import statistics
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from costlens.db.tenant import set_tenant
from costlens.models import CostAnomaly, CostDaily, Organization

HISTORY_DAYS = 28
MIN_HISTORY = 14
SENSITIVITY_K = {"high": 2.5, "medium": 3.0, "low": 4.0}
MIN_RELATIVE = 0.10


class AlertSettings(BaseModel):
    anomaly_alerts: bool = True
    sensitivity: Literal["low", "medium", "high"] = "medium"
    min_impact: float = Field(default=50.0, ge=0, le=1_000_000)  # in the org's currency
    # Reword explanations with AI (sends the summary, never raw billing data, to Anthropic).
    ai_explanations: bool = False
    # Early warnings (5.8): month-end forecast well above last month; today's spend so far
    # well above the same hours on recent days (needs hourly data from the billing export).
    forecast_warnings: bool = True
    forecast_over_pct: float = Field(default=10.0, ge=1, le=500)
    same_day_warnings: bool = True
    same_day_over_pct: float = Field(default=30.0, ge=5, le=1000)
    # Weekly digest (6.1): also email it to owners and admins (channels subscribe per event).
    weekly_digest_email: bool = False


def alert_settings(org: Organization) -> AlertSettings:
    return AlertSettings.model_validate((org.settings or {}).get("alerts", {}))


@dataclass
class Finding:
    day: date
    dimension: str
    key: str
    currency: str
    actual: float
    expected: float
    upper: float
    score: float

    @property
    def impact(self) -> float:
        return self.actual - self.expected


def baseline(history: list[tuple[date, float]], day: date) -> tuple[float, float] | None:
    """(expected, robust spread) for ``day`` from up to 28 previous days."""
    values = [v for _, v in history]
    if len(values) < MIN_HISTORY:
        return None
    overall = statistics.median(values)
    by_weekday: dict[int, list[float]] = defaultdict(list)
    for d, v in history:
        by_weekday[d.weekday()].append(v)

    def factor(wd: int) -> float:
        same = by_weekday.get(wd)
        return statistics.median(same) / overall if same and overall > 0 else 1.0

    expected = overall * factor(day.weekday())
    residuals = [v - overall * factor(d.weekday()) for d, v in history]
    center = statistics.median(residuals)
    mad = statistics.median(abs(r - center) for r in residuals)
    spread = max(1.4826 * mad, 0.02 * max(expected, 0.0))  # floor: 2% of expected
    return expected, spread


def detect_series(
    series: dict[date, float],
    days: list[date],
    *,
    k: float,
    min_impact: float,
    dimension: str,
    key: str,
    currency: str,
) -> list[Finding]:
    found = []
    for day in days:
        start = day - timedelta(days=HISTORY_DAYS)
        history = [
            (start + timedelta(days=i), series.get(start + timedelta(days=i), 0.0))
            for i in range(HISTORY_DAYS)
        ]
        history = [(d, v) for d, v in history if d >= min(series, default=day)]
        base = baseline(history, day)
        if base is None:
            continue
        expected, spread = base
        actual = series.get(day, 0.0)
        upper = expected + k * spread
        extra = actual - expected
        if actual > upper and extra >= min_impact and extra >= MIN_RELATIVE * max(expected, 0.01):
            found.append(
                Finding(
                    day,
                    dimension,
                    key,
                    currency,
                    round(actual, 2),
                    round(expected, 2),
                    round(upper, 2),
                    round(extra / spread if spread else 99.0, 1),
                )
            )
    return found


DIMENSION_COLUMNS: dict[str, Any] = {
    "service": CostDaily.service_name,
    "account": CostDaily.sub_account_id,
}


def detect(
    db: Session,
    org_id: uuid.UUID,
    *,
    through: date | None = None,
    recent_days: int = 3,
    settings: AlertSettings | None = None,
) -> list[CostAnomaly]:
    """Find anomalies on the last ``recent_days`` days with data; store new ones."""
    set_tenant(db, org_id=org_id)
    org = db.get(Organization, org_id)
    if org is None:
        return []
    settings = settings or alert_settings(org)
    k = SENSITIVITY_K[settings.sensitivity]
    through = through or db.scalar(
        select(func.max(CostDaily.charge_date)).where(CostDaily.org_id == org_id)
    )
    if through is None:
        return []
    days = [through - timedelta(days=i) for i in range(recent_days - 1, -1, -1)]
    start = days[0] - timedelta(days=HISTORY_DAYS)
    amount = CostDaily.effective_cost
    usage_only = CostDaily.charge_category.in_(("Usage", "Adjustment"))
    findings: list[Finding] = []

    totals = db.execute(
        select(CostDaily.billing_currency, CostDaily.charge_date, func.sum(amount))
        .where(
            CostDaily.org_id == org_id,
            CostDaily.charge_date >= start,
            CostDaily.charge_date <= through,
            usage_only,
        )
        .group_by(CostDaily.billing_currency, CostDaily.charge_date)
    ).all()
    by_currency: dict[str, dict[date, float]] = defaultdict(dict)
    for currency, day, value in totals:
        by_currency[currency][day] = float(value or 0)
    for currency, series in by_currency.items():
        findings += detect_series(
            series,
            days,
            k=k,
            min_impact=settings.min_impact,
            dimension="total",
            key="All spend",
            currency=currency,
        )

    for dimension, column in DIMENSION_COLUMNS.items():
        rows = db.execute(
            select(CostDaily.billing_currency, column, CostDaily.charge_date, func.sum(amount))
            .where(
                CostDaily.org_id == org_id,
                CostDaily.charge_date >= start,
                CostDaily.charge_date <= through,
                usage_only,
            )
            .group_by(CostDaily.billing_currency, column, CostDaily.charge_date)
        ).all()
        grouped: dict[tuple[str, str], dict[date, float]] = defaultdict(dict)
        for currency, key, day, value in rows:
            grouped[(currency, str(key) if key is not None else "(none)")][day] = float(value or 0)
        for (currency, key), series in grouped.items():
            findings += detect_series(
                series,
                days,
                k=k,
                min_impact=settings.min_impact,
                dimension=dimension,
                key=key,
                currency=currency,
            )

    findings = _explained_away(db, org_id, findings)
    return _record(db, org_id, findings)


EXPLAINED = 0.7  # a parent increase is "explained" when services account for 70% of it


def _explained_away(db: Session, org_id: uuid.UUID, findings: list[Finding]) -> list[Finding]:
    """Drop total/account findings that service findings on the same day already explain,
    so one incident is reported once, at its most specific level."""
    services = [f for f in findings if f.dimension == "service"]
    if not services:
        return findings
    keep = []
    for f in findings:
        same_day = [s for s in services if s.day == f.day and s.currency == f.currency]
        if f.dimension == "total":
            explained = sum(s.impact for s in same_day)
        elif f.dimension == "account":
            explained = 0.0
            for svc in same_day:
                share = (
                    db.scalar(
                        select(func.sum(CostDaily.effective_cost)).where(
                            CostDaily.org_id == org_id,
                            CostDaily.charge_date == f.day,
                            CostDaily.service_name == svc.key,
                            CostDaily.sub_account_id == f.key,
                            CostDaily.billing_currency == f.currency,
                        )
                    )
                    or 0
                )
                explained += svc.impact * (float(share) / svc.actual if svc.actual else 0)
        else:
            keep.append(f)
            continue
        if explained < EXPLAINED * f.impact:
            keep.append(f)
    return keep


def _record(db: Session, org_id: uuid.UUID, findings: list[Finding]) -> list[CostAnomaly]:
    """Store findings; consecutive days of the same series extend one episode."""
    created = []
    for f in sorted(findings, key=lambda x: x.day):
        episode = db.scalar(
            select(CostAnomaly).where(
                CostAnomaly.dimension == f.dimension,
                CostAnomaly.key == f.key[:300],
                CostAnomaly.currency == f.currency,
                CostAnomaly.status.in_(("open", "acknowledged")),
                CostAnomaly.last_day >= f.day - timedelta(days=1),
                CostAnomaly.day <= f.day,
            )
        )
        if episode is not None:
            if f.day > episode.last_day:  # a new day of the same episode
                episode.last_day, episode.days = f.day, episode.days + 1
                episode.impact = round(episode.impact + f.impact, 2)
            elif f.day == episode.last_day and f.day == episode.day:  # re-run of the same day
                episode.impact = round(f.impact, 2)
            episode.actual, episode.expected, episode.upper = f.actual, f.expected, f.upper
            episode.peak_impact = round(max(episode.peak_impact, f.impact), 2)
            episode.score = max(episode.score, f.score)
            continue
        anomaly = CostAnomaly(
            org_id=org_id,
            day=f.day,
            last_day=f.day,
            days=1,
            dimension=f.dimension,
            key=f.key[:300],
            currency=f.currency,
            actual=f.actual,
            expected=f.expected,
            upper=f.upper,
            impact=round(f.impact, 2),
            peak_impact=round(f.impact, 2),
            score=f.score,
            status="open",
        )
        db.add(anomaly)
        db.flush()
        created.append(anomaly)
    db.flush()
    return created
