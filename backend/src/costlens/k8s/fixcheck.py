"""Did the fix work? Compare a workload before the incident, during it and after the fix,
and give a clear verdict."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from statistics import mean
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from costlens.k8s.correlate import series
from costlens.k8s.health import workload_health
from costlens.models import K8sCluster, K8sEvent

FAILURE_REASONS = (
    "BackOff",
    "OOMKilling",
    "OOMKilled",
    "Unhealthy",
    "Failed",
    "FailedScheduling",
    "FailedMount",
    "Evicted",
    "CrashLoopBackOff",
)
MIN_AFTER = timedelta(hours=1)
BASELINE = timedelta(hours=24)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _avg(values: dict[datetime, float], start: datetime, end: datetime) -> float | None:
    picked = [v for h, v in values.items() if start <= h < end]
    return mean(picked) if picked else None


def _failure_rate(
    db: Session, cluster: K8sCluster, namespace: str, name: str, start: datetime, end: datetime
) -> tuple[float, int]:
    """(failure events per hour, total) between start and end, by event last-seen time."""
    total = 0
    for e in db.scalars(
        select(K8sEvent).where(
            K8sEvent.cluster_id == cluster.id,
            K8sEvent.namespace == namespace,
            K8sEvent.type == "Warning",
            K8sEvent.reason.in_(FAILURE_REASONS),
            K8sEvent.last_at >= start,
            K8sEvent.last_at < end,
        )
    ):
        if (e.object_name or "").startswith(name):
            total += e.count
    hours = max((end - start).total_seconds() / 3600, 1 / 60)
    return total / hours, total


def check(
    db: Session,
    cluster: K8sCluster,
    *,
    namespace: str,
    kind: str,
    name: str,
    fix_at: datetime,
    now: datetime,
    incident_start: datetime | None = None,
) -> dict[str, Any]:
    fix_at, now = _aware(fix_at), _aware(now)
    if incident_start is None:  # first failure warning in the day before the fix
        first = db.scalar(
            select(K8sEvent.first_at)
            .where(
                K8sEvent.cluster_id == cluster.id,
                K8sEvent.namespace == namespace,
                K8sEvent.type == "Warning",
                K8sEvent.reason.in_(FAILURE_REASONS),
                K8sEvent.last_at >= fix_at - BASELINE,
                K8sEvent.first_at <= fix_at,
                K8sEvent.object_name.like(f"{name}%"),
            )
            .order_by(K8sEvent.first_at)
        )
        incident_start = _aware(first) if first else fix_at - timedelta(hours=1)
    incident_start = min(_aware(incident_start), fix_at)
    periods = {
        "before": (incident_start - BASELINE, incident_start),
        "during": (incident_start, fix_at),
        "after": (fix_at, now),
    }
    data = series(db, cluster, namespace, kind, name, incident_start - BASELINE, now)
    rows = []
    for key, (start, end) in periods.items():
        rate, total = _failure_rate(db, cluster, namespace, name, start, end)
        rows.append(
            {
                "period": key,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "failures_per_hour": round(rate, 2),
                "failures": total,
                "cpu": _round(_avg(data["cpu"], start, end)),
                "memory_gib": _round(_avg(data["memory"], start, end)),
                "cost_per_hour": _round(_avg(data["cost"], start, end)),
            }
        )
    before, during, after = rows
    health = next(
        (
            w
            for w in workload_health(db, cluster.id)
            if (w["namespace"], w["kind"], w["name"]) == (namespace, kind, name)
        ),
        None,
    )
    status = health["health"] if health else "gone"
    verdict, text = _verdict(now - fix_at, fix_at, during, after, status)
    side_effects = []
    for field, label in (
        ("memory_gib", "memory per replica"),
        ("cpu", "CPU per replica"),
        ("cost_per_hour", "allocated cost per hour"),
    ):
        b, a = before[field], after[field]
        if b and a and abs(a - b) / b >= 0.2:
            side_effects.append(
                f"{label} is {abs(a - b) / b * 100:.0f}% {'higher' if a > b else 'lower'} "
                f"than before the incident"
            )
    return {
        "verdict": verdict,
        "text": text,
        "health_now": status,
        "fix_at": fix_at.isoformat(),
        "incident_start": incident_start.isoformat(),
        "periods": rows,
        "side_effects": side_effects,
    }


def _verdict(
    observed: timedelta,
    fix_at: datetime,
    during: dict[str, Any],
    after: dict[str, Any],
    status: str,
) -> tuple[str, str]:
    if observed < MIN_AFTER:
        return "too_early", (
            f"Too early to tell: check again after {(fix_at + MIN_AFTER):%H:%M} "
            "(at least an hour after the fix)."
        )
    if after["failures"] == 0 and status in ("ok", "gone"):
        return "fixed", (
            f"Fixed: no failures in the {_hours(observed)} since the fix and the "
            "workload is healthy."
        )
    halved = (
        during["failures_per_hour"] and after["failures_per_hour"] < during["failures_per_hour"] / 2
    )
    if halved and status != "failing":
        return "improving", (
            f"Improving: failures dropped from {during['failures_per_hour']:.1f} to "
            f"{after['failures_per_hour']:.1f} per hour, but they have not stopped."
        )
    return "not_fixed", (
        f"Not fixed: the workload is still {status} with "
        f"{after['failures_per_hour']:.1f} failures per hour after the fix."
    )


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def _hours(delta: timedelta) -> str:
    hours = delta.total_seconds() / 3600
    return f"{hours:.0f} hours" if hours >= 2 else f"{delta.total_seconds() / 60:.0f} minutes"
