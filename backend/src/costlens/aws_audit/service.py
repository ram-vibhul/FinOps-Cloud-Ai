"""Run the AWS audit: every check in the catalogue ends as *fine*, *saving*, *more_data*
or *manual*, with a reason. Services in the bill that no check covers get a manual row."""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from costlens.aws_audit.catalogue import AREAS, CHECKS, Check
from costlens.aws_audit.inspections import INSPECTIONS
from costlens.costs.fx import converted
from costlens.db.base import utcnow
from costlens.models import (
    AuditResult,
    AuditRun,
    CloudResource,
    CostDaily,
    Finding,
    Organization,
    ResourceMetric,
)

STATUSES = ("saving", "more_data", "manual", "fine")
ACTIVE = ("open", "approved", "applied")
WINDOW = 30
MIN_SPEND = 1.0  # monthly: below this a service counts as not used


def _money(v: float, currency: str) -> str:
    return f"{v:,.2f} {currency}"


class Facts:
    """Everything the checks need, loaded once."""

    def __init__(self, db: Session, org_id: uuid.UUID, currency: str) -> None:
        self._db, self._org_id, self.currency = db, org_id, currency
        self._bucket_costs: dict[str, dict[str, float]] | None = None
        last = db.scalar(select(func.max(CostDaily.charge_date)).where(CostDaily.provider == "aws"))
        self.has_bill = last is not None
        self.spend: dict[str, float] = {}
        if last is not None:
            amount = converted(
                CostDaily.effective_cost,
                CostDaily.billing_currency,
                CostDaily.charge_date,
                currency,
                CostDaily.org_id,
            )
            rows = db.execute(
                select(CostDaily.service_name, func.sum(amount))
                .where(
                    CostDaily.provider == "aws",
                    CostDaily.charge_date > last - timedelta(days=WINDOW),
                )
                .group_by(CostDaily.service_name)
            ).all()
            self.spend = {name: float(v or 0) for name, v in rows}
        self.resources: dict[str, list[str]] = defaultdict(list)
        self.objects: dict[str, list[CloudResource]] = defaultdict(list)
        for r in db.scalars(
            select(CloudResource).where(
                CloudResource.provider == "aws", CloudResource.state != "deleted"
            )
        ):
            self.resources[r.resource_type].append(r.resource_id)
            self.objects[r.resource_type].append(r)
        since = (utcnow() - timedelta(days=14)).date()
        self.measured = set(
            db.scalars(
                select(ResourceMetric.resource_id).where(ResourceMetric.day >= since).distinct()
            )
        )
        self.findings: dict[str, list[Finding]] = defaultdict(list)
        for f in db.scalars(select(Finding).where(Finding.status.in_(ACTIVE))):
            self.findings[f.detector].append(f)

    def bucket_costs(self) -> dict[str, dict[str, float]]:
        if self._bucket_costs is None:
            from costlens.findings.aws_storage import bucket_costs

            self._bucket_costs = bucket_costs(self._db, self._org_id, self.currency)
        return self._bucket_costs

    def usage_costs(
        self, ids: list[str], classify: Callable[[str | None], str]
    ) -> dict[str, dict[str, float]]:
        from costlens.findings.billing import usage_costs

        return usage_costs(self._db, self._org_id, self.currency, ids, classify)

    def pattern_costs(self, patterns: dict[str, str]) -> dict[str, float]:
        from costlens.findings.billing import usage_type_costs

        return usage_type_costs(self._db, self._org_id, self.currency, patterns)

    def services_for(self, check: Check) -> list[str]:
        return [s for s in self.spend if any(b in s.lower() for b in check.billing)]


def evaluate(  # noqa: PLR0911 - one return per outcome reads best
    check: Check, facts: Facts, errors: dict[str, str], currency: str
) -> dict[str, Any]:
    services = facts.services_for(check)
    spend = sum(facts.spend[s] for s in services)
    rids = [r for t in check.resource_types for r in facts.resources.get(t, [])]
    row: dict[str, Any] = {
        "check_id": check.id,
        "area": check.area,
        "service": check.service,
        "title": check.title,
        "spend_monthly": round(spend, 2),
        "savings_monthly": 0.0,
        "finding_count": 0,
        "details": {"billing_services": services, "resources": len(rids)},
    }

    def done(status: str, reason: str) -> dict[str, Any]:
        return {**row, "status": status, "reason": reason}

    findings = [f for d in check.detectors for f in facts.findings.get(d, [])]
    if findings:
        best: dict[str, float] = {}
        for f in findings:
            best[f.overlap_key] = max(best.get(f.overlap_key, 0.0), float(f.savings_monthly))
        row["savings_monthly"] = round(sum(best.values()), 2)
        row["finding_count"] = len(findings)
        row["details"]["finding_ids"] = [str(f.id) for f in findings[:50]]
        return done(
            "saving",
            f"{len(findings)} suggestion{'s' if len(findings) != 1 else ''} worth "
            f"{_money(row['savings_monthly'], currency)} a month (see Savings).",
        )
    failed = [errors[d] for d in check.detectors if d in errors]
    if failed:
        return done("more_data", f"The check could not run this time ({failed[0]}); try again.")
    if not facts.has_bill:
        return done(
            "more_data",
            "No AWS bill imported yet: connect the Cost and Usage Report "
            "(Settings → Cloud accounts).",
        )
    if spend < MIN_SPEND and not rids:
        return done("fine", f"Not used: no spend in the last {WINDOW} days.")
    if not check.detectors and not check.inspect:
        return done("manual", f"Not checked automatically yet. {check.manual}")
    notes = INSPECTIONS[check.inspect](facts) if check.inspect else []
    if notes:
        row["details"]["notes"] = notes
        return done("manual", "Review: " + " ".join(notes))
    if check.resource_types and not rids and facts.resources:
        kinds = ", ".join(t.replace("_", " ") for t in check.resource_types)
        return done("fine", f"None found in the inventory ({kinds}); nothing to change.")
    if check.resource_types and not rids:
        return done(
            "more_data",
            f"{_money(spend, currency)} spent but no resources in the inventory: give "
            f"CostLens read access to {check.service} in the connected accounts.",
        )
    if check.metrics and not any(r in facts.measured for r in rids):
        return done(
            "more_data",
            "No CloudWatch usage collected yet for these resources; at least 7 days are "
            "needed before suggesting changes.",
        )
    if check.metrics:
        unmeasured = sum(1 for r in rids if r not in facts.measured)
        if unmeasured:
            row["details"]["unmeasured"] = unmeasured
            return done(
                "fine",
                f"Checked {len(rids) - unmeasured} of {len(rids)} resource(s) with usage "
                f"data; nothing to change. {unmeasured} have no usage data yet.",
            )
    what = f"{len(rids)} resource(s)" if check.resource_types else "the bill"
    return done("fine", f"Checked {what}; nothing to change.")


def uncovered(facts: Facts, currency: str) -> list[dict[str, Any]]:
    """Services with spend that no check looks at: never silently skipped."""
    covered = {s for c in CHECKS for s in facts.services_for(c)}
    return [
        {
            "check_id": f"other:{name[:70]}",
            "area": "other",
            "service": name,
            "title": "Not covered by an automatic check",
            "status": "manual",
            "reason": f"{_money(spend, currency)} in the last {WINDOW} days and no check "
            "covers this service yet: review its usage by hand.",
            "spend_monthly": round(spend, 2),
            "savings_monthly": 0.0,
            "finding_count": 0,
            "details": {"billing_services": [name]},
        }
        for name, spend in sorted(facts.spend.items(), key=lambda x: -x[1])
        if name not in covered and spend >= MIN_SPEND
    ]


def run_audit(
    db: Session,
    org_id: uuid.UUID,
    *,
    user_id: uuid.UUID | None = None,
    trigger: str = "manual",
    run_detectors: bool = True,
) -> AuditRun:
    org = db.get(Organization, org_id)
    currency = org.default_currency if org else "USD"
    run = AuditRun(
        org_id=org_id,
        started_at=utcnow(),
        trigger=trigger,
        requested_by_id=user_id,
        currency=currency,
        status="running",
    )
    db.add(run)
    db.flush()
    errors: dict[str, str] = {}
    if run_detectors:
        from costlens.findings.service import run as run_findings

        results = run_findings(db, org_id)
        errors = {k: v["error"] for k, v in results.items() if "error" in v}
    facts = Facts(db, org_id, currency)
    rows = [evaluate(c, facts, errors, currency) for c in CHECKS] + uncovered(facts, currency)
    previous = _previous(db, run)
    for r in rows:
        before = previous.get(r["check_id"])
        if before and before != r["status"]:
            r["details"]["previous_status"] = before
        db.add(AuditResult(org_id=org_id, run_id=run.id, **r))
    run.summary = summarise(rows)
    # Findings on the same resource from different checks count once.
    best: dict[str, float] = {}
    for c in CHECKS:
        for d in c.detectors:
            for f in facts.findings.get(d, []):
                best[f.overlap_key] = max(best.get(f.overlap_key, 0.0), float(f.savings_monthly))
    run.summary["savings_monthly"] = round(sum(best.values()), 2)
    run.status, run.finished_at = "done", utcnow()
    db.flush()
    return run


def _previous(db: Session, run: AuditRun) -> dict[str, str]:
    prev = db.scalar(
        select(AuditRun.id)
        .where(AuditRun.id != run.id, AuditRun.status == "done")
        .order_by(AuditRun.started_at.desc())
        .limit(1)
    )
    if prev is None:
        return {}
    return dict(
        db.execute(
            select(AuditResult.check_id, AuditResult.status).where(AuditResult.run_id == prev)
        ).all()
    )


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {s: 0 for s in STATUSES}
    by_area: dict[str, dict[str, int]] = {}
    for r in rows:
        counts[r["status"]] += 1
        area = by_area.setdefault(r["area"], {s: 0 for s in STATUSES})
        area[r["status"]] += 1
    return {
        "checks": len(rows),
        "counts": counts,
        "by_area": by_area,
        "savings_monthly": round(sum(r["savings_monthly"] for r in rows), 2),
        "uncovered_spend_monthly": round(
            sum(r["spend_monthly"] for r in rows if r["check_id"].startswith("other:")), 2
        ),
        "changed": sum(1 for r in rows if "previous_status" in r["details"]),
        "areas": AREAS,
    }
