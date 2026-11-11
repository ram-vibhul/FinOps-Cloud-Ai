"""Cause and effect for one workload: cost, CPU, memory and warnings per hour on one
timeline with deployments and cost anomalies, and the before/after effect of the change
that most likely started the trouble."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from statistics import mean
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ccradar.costs.fx import converted
from ccradar.models import (
    ChangeEvent,
    CostAnomaly,
    K8sCluster,
    K8sCost,
    K8sEvent,
    K8sUsage,
    Organization,
)

LOOKBACK = timedelta(hours=48)  # shown before the window
HISTORY = timedelta(days=8)  # fetched for week-over-week baselines
SIDE = timedelta(hours=6)  # compare 6 hours before vs after the anchor
MIN_CHANGE = 0.20
SIGNALS = {
    "cost": ("allocated cost per hour", "money"),
    "cpu": ("CPU per replica", "cores"),
    "memory": ("memory per replica", "GiB"),
    "warnings": ("warning events per hour", "count"),
}


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _hour(dt: datetime) -> datetime:
    return _aware(dt).replace(minute=0, second=0, microsecond=0)


def series(
    db: Session,
    cluster: K8sCluster,
    namespace: str,
    kind: str,
    name: str,
    start: datetime,
    end: datetime,
) -> dict[str, dict[datetime, float]]:
    """Hourly signals for the workload between ``start`` and ``end``."""
    org = db.get(Organization, cluster.org_id)
    currency = org.default_currency if org else "USD"
    out: dict[str, dict[datetime, float]] = {k: {} for k in SIGNALS}
    t = K8sCost
    amount = converted(t.cost, t.currency, func.date(t.hour), currency, t.org_id)
    for hour, cost in db.execute(
        select(t.hour, func.sum(amount))
        .where(
            t.cluster_id == cluster.id,
            t.namespace == namespace,
            t.kind == kind,
            t.name == name,
            t.hour >= start,
            t.hour <= end,
        )
        .group_by(t.hour)
    ):
        out["cost"][_hour(hour)] = float(cost or 0)
    per_hour: dict[datetime, dict[str, float]] = defaultdict(lambda: {"cpu": 0.0, "memory": 0.0})
    for u in db.scalars(
        select(K8sUsage).where(
            K8sUsage.cluster_id == cluster.id,
            K8sUsage.namespace == namespace,
            K8sUsage.kind == kind,
            K8sUsage.name == name,
            K8sUsage.hour >= start,
            K8sUsage.hour <= end,
        )
    ):
        n = u.samples or 1
        per_hour[_hour(u.hour)]["cpu"] += u.cpu_sum / n  # summed over containers: per replica
        per_hour[_hour(u.hour)]["memory"] += u.memory_sum / n
    for hour, v in per_hour.items():
        out["cpu"][hour], out["memory"][hour] = v["cpu"], v["memory"]
    for e in db.scalars(
        select(K8sEvent).where(
            K8sEvent.cluster_id == cluster.id,
            K8sEvent.namespace == namespace,
            K8sEvent.type == "Warning",
            K8sEvent.last_at >= start,
            K8sEvent.first_at <= end,
            or_(K8sEvent.object_name.like(f"{name}%"), K8sEvent.object_name == name),
        )
    ):
        h = _hour(e.last_at)
        out["warnings"][h] = out["warnings"].get(h, 0) + e.count
    return out


def _compare(
    values: dict[datetime, float], anchor: datetime, end: datetime
) -> tuple[float, float] | None:
    """Mean after the anchor vs a baseline: the same clock hours one week earlier (so daily
    and weekday/weekend cycles do not look like an effect), else one day earlier, else the
    hours just before the anchor."""
    after_hours = [h for h in values if anchor <= h <= min(end, anchor + SIDE)]
    if not after_hours:
        return None
    for shift in (timedelta(days=7), timedelta(days=1)):
        pairs = [
            (values[h - shift], values[h])
            for h in after_hours
            if h - shift in values and h - shift < anchor
        ]
        if len(pairs) * 2 >= len(after_hours):
            return mean(b for b, _ in pairs), mean(a for _, a in pairs)
    before = [v for h, v in values.items() if anchor - SIDE <= h < anchor]
    if not before:
        return None
    return mean(before), mean(values[h] for h in after_hours)


def _fmt(value: float, unit: str, currency: str) -> str:
    if unit == "money":
        return f"{value:,.2f} {currency}"
    if unit == "GiB":
        return f"{value:.2f} GiB"
    if unit == "cores":
        return f"{value:.2f} cores"
    return f"{value:.0f}"


def _effect(
    key: str, values: dict[datetime, float], after_from: datetime, end: datetime, currency: str
) -> dict[str, Any] | None:
    """Before/after effect of one signal around the anchor, if it changed meaningfully."""
    label, unit = SIGNALS[key]
    if key == "warnings":
        total = sum(v for h, v in values.items() if h >= after_from - timedelta(hours=1))
        if not total:
            return None
        return {
            "signal": key,
            "label": label,
            "before": 0,
            "after": total,
            "change_pct": None,
            "text": f"{total:.0f} warning event(s) since the change",
        }
    pair = _compare(values, after_from, end)
    if pair is None:
        return None
    before, after = pair
    change = (after - before) / (before or 1e-9)
    if abs(change) < MIN_CHANGE or abs(after - before) <= (0.02 if unit == "GiB" else 0.01):
        return None
    direction = "up" if change > 0 else "down"
    return {
        "signal": key,
        "label": label,
        "before": round(before, 4),
        "after": round(after, 4),
        "change_pct": round(change * 100),
        "text": f"{label} {direction} {abs(change) * 100:.0f}% "
        f"({_fmt(before, unit, currency)} → {_fmt(after, unit, currency)})",
    }


def correlate(
    db: Session,
    cluster: K8sCluster,
    namespace: str,
    kind: str,
    name: str,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    org = db.get(Organization, cluster.org_id)
    currency = org.default_currency if org else "USD"
    begin = start - LOOKBACK
    full = series(db, cluster, namespace, kind, name, start - HISTORY, end)  # for baselines
    data = {k: {h: v for h, v in d.items() if h >= _hour(begin)} for k, d in full.items()}
    changes = list(
        db.scalars(
            select(ChangeEvent)
            .where(
                ChangeEvent.occurred_at >= begin,
                ChangeEvent.occurred_at <= end,
                or_(
                    *[
                        col.ilike(f"%{term}%")
                        for term in (name, namespace)
                        for col in (
                            ChangeEvent.title,
                            ChangeEvent.resource_id,
                            ChangeEvent.service,
                            ChangeEvent.environment,
                        )
                    ]
                ),
            )
            .order_by(ChangeEvent.occurred_at)
        )
    )
    first_warning = min(data["warnings"], default=None)
    # Anchor: the last change before trouble started (or the last change in the window).
    candidates = [
        c for c in changes if first_warning is None or _aware(c.occurred_at) <= first_warning
    ]
    anchor_change = candidates[-1] if candidates else (changes[-1] if changes else None)
    effects: list[dict[str, Any]] = []
    story: list[str] = []
    if anchor_change is not None:
        anchor = _aware(anchor_change.occurred_at)
        story.append(f"{anchor:%d %b %H:%M} — change: {anchor_change.title}")
        effects = [
            e
            for key in SIGNALS
            if (
                e := _effect(
                    key,
                    (data if key == "warnings" else full)[key],
                    _hour(anchor) + timedelta(hours=1),
                    end,
                    currency,
                )
            )
        ]
        story += [f"→ {e['text']}" for e in effects]
    if first_warning is not None:
        story.append(f"{first_warning:%d %b %H:%M} — first warnings for {kind} {name}")
    anomalies = db.scalars(
        select(CostAnomaly)
        .where(CostAnomaly.last_day >= begin.date(), CostAnomaly.day <= end.date())
        .order_by(CostAnomaly.day)
    ).all()
    hours = sorted({h for s in data.values() for h in s})
    return {
        "currency": currency,
        "anchor": None
        if anchor_change is None
        else {
            "at": _aware(anchor_change.occurred_at).isoformat(),
            "title": anchor_change.title,
            "source": anchor_change.source,
            "url": anchor_change.url,
        },
        "first_warning": first_warning.isoformat() if first_warning else None,
        "effects": effects,
        "story": story,
        "series": {
            "hours": [h.isoformat() for h in hours],
            **{
                k: [round(data[k].get(h, 0.0), 4) if h in data[k] else None for h in hours]
                for k in ("cost", "cpu", "memory", "warnings")
            },
        },
        "markers": [
            {"at": _aware(c.occurred_at).isoformat(), "label": c.title, "type": "change"}
            for c in changes
        ]
        + (
            [{"at": first_warning.isoformat(), "label": "First warnings", "type": "incident"}]
            if first_warning
            else []
        ),
        "cost_anomalies": [
            {
                "day": a.day.isoformat(),
                "label": a.key,
                "impact": round(a.impact, 2),
                "currency": a.currency,
            }
            for a in anomalies[:5]
        ],
    }


def attach(
    db: Session,
    cluster: K8sCluster,
    result: dict[str, Any],
    namespace: str | None,
    kind: str | None,
    name: str | None,
    start: datetime,
    end: datetime,
) -> None:
    """Add the cause-and-effect view to an investigation of a single workload."""
    if namespace and kind and name:
        result["correlation"] = correlate(db, cluster, namespace, kind, name, start, end)
