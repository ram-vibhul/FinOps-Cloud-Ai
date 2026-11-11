"""Customer PromQL queries: storage of the values the agent sends back."""

from __future__ import annotations

import math
import uuid
from typing import Any

from ccradar.core.errors import AppError
from ccradar.core.redact import redact
from ccradar.db.base import utcnow
from ccradar.models import K8sCluster, K8sMetricPoint, K8sMetricQuery

MAX_LABELS = 10


def series_key(labels: dict[str, str]) -> str:
    return ",".join(f"{k}={v}" for k, v in sorted(labels.items()))[:300] or "value"


def store(
    db: Any,
    cluster: K8sCluster,
    query_id: str,
    ok: bool,
    error: str | None,
    series: list[dict[str, Any]],
) -> int:
    try:
        qid = uuid.UUID(query_id)
    except ValueError as exc:
        raise AppError("Unknown query.", status=404, code="unknown_query") from exc
    query = db.get(K8sMetricQuery, qid)
    if query is None or query.cluster_id != cluster.id:
        raise AppError("Unknown query.", status=404, code="unknown_query")
    now = utcnow()
    query.last_run_at = now
    if not ok:
        query.last_error = redact(error or "failed")[:500]
        db.flush()
        return 0
    query.last_error = None
    stored = 0
    for s in series[:50]:
        value = float(s.get("value", 0))
        if not math.isfinite(value):
            continue
        labels = {
            str(k)[:100]: redact(str(v))[:200]
            for k, v in list((s.get("labels") or {}).items())[:MAX_LABELS]
        }
        db.add(
            K8sMetricPoint(
                org_id=cluster.org_id,
                cluster_id=cluster.id,
                query_id=qid,
                at=now,
                series=series_key(labels),
                labels=labels,
                value=value,
            )
        )
        stored += 1
    db.flush()
    return stored
