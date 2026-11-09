"""How often the top-ranked cause was the real one, from closed investigations, next to
the replay lab score."""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from costlens.k8s.investigate import CHECK_LABELS
from costlens.models import K8sInvestigation

_LAB: dict[str, Any] = {"at": 0.0, "report": None}
LAB_TTL = 3600


def lab_score() -> dict[str, Any] | None:
    if _LAB["report"] is None or time.monotonic() - _LAB["at"] > LAB_TTL:
        from costlens.k8s import lab

        report = lab.run()
        _LAB.update(
            at=time.monotonic(),
            report={
                "accuracy": report["accuracy"],
                "correct": report["correct"],
                "scenarios": report["scenarios"],
                "kind": "replay",
            },
        )
    return _LAB["report"]


def _pct(part: int, whole: int) -> float | None:
    return round(part / whole * 100, 1) if whole else None


def accuracy(db: Session) -> dict[str, Any]:
    closed = db.scalars(
        select(K8sInvestigation).where(K8sInvestigation.real_cause.is_not(None))
    ).all()
    n = len(closed)
    top1 = sum(1 for i in closed if i.cause_rank == 1)
    top3 = sum(1 for i in closed if i.cause_rank is not None and i.cause_rank <= 3)
    per: dict[str, dict[str, int]] = defaultdict(
        lambda: {"ranked_first": 0, "right_when_first": 0, "real": 0, "found_first": 0}
    )
    months: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for i in closed:
        if i.top_cause:
            per[i.top_cause]["ranked_first"] += 1
            per[i.top_cause]["right_when_first"] += i.top_cause == i.real_cause
        if i.real_cause:
            per[i.real_cause]["real"] += 1
            per[i.real_cause]["found_first"] += i.cause_rank == 1
        if i.closed_at:
            m = months[i.closed_at.strftime("%Y-%m")]
            m[0] += 1
            m[1] += i.cause_rank == 1
    checks = [
        {
            "check": key,
            "label": CHECK_LABELS.get(key, "Other"),
            "ranked_first": v["ranked_first"],
            "real": v["real"],
            # precision: when CostLens said X first, how often X was right
            "precision_pct": _pct(v["right_when_first"], v["ranked_first"]),
            # recall: when X was the real cause, how often CostLens ranked it first
            "recall_pct": _pct(v["found_first"], v["real"]),
        }
        for key, v in sorted(per.items(), key=lambda kv: -(kv[1]["real"] + kv[1]["ranked_first"]))
    ]
    return {
        "closed": n,
        "top1_pct": _pct(top1, n),
        "top3_pct": _pct(top3, n),
        "missed": sum(1 for i in closed if i.cause_rank is None),
        "checks": checks,
        "by_month": [
            {"month": k, "closed": v[0], "top1_pct": _pct(v[1], v[0])}
            for k, v in sorted(months.items())
        ],
        "lab": lab_score(),
        "causes": [{"check": k, "label": v} for k, v in CHECK_LABELS.items()]
        + [{"check": "other", "label": "Something else"}],
    }
