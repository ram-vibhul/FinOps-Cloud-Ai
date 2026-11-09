"""Storage detectors: S3 Intelligent-Tiering for cool buckets and EFS lifecycle to
Infrequent Access. S3 costs per bucket are split by storage class from the bill."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from costlens.costs import fx
from costlens.db.base import Base
from costlens.findings.aws import MONTH, _common, _resources
from costlens.findings.aws_compute import _usd
from costlens.findings.registry import FindingIn, detector
from costlens.inventory.service import resource_costs

MIN_SAVING = 1.0
IT_MIN_STANDARD = 20.0  # monthly Standard storage before Intelligent-Tiering is worth it
IT_MAX_REQUEST_SHARE = 0.20  # requests above this share of storage cost: data is busy
IT_SAVING = 0.20  # conservative: tiers 30/90 days unread save 40-68%
EFS_STANDARD_USD = 0.30  # per GB-month (us-east-1, list price)
EFS_IA_USD = 0.016
EFS_COLD_SHARE = 0.30  # assumed share of data not read for 30 days (stated)
EFS_MIN_STANDARD_GB = 50


def s3_class(usage_type: str | None) -> str:  # noqa: PLR0911 - a lookup table
    u = (usage_type or "").lower()
    if "earlydelete" in u:
        return "early_delete"
    if "timedstorage" in u:
        if "-int" in u:
            return "intelligent_tiering"
        if "glacier" in u or "-gir" in u or "-gda" in u:
            return "archive"
        if "-sia" in u or "-zia" in u:
            return "infrequent"
        return "standard"
    if "retrieval" in u or "restore" in u or "glacier" in u:
        return "retrieval"
    if "requests" in u:
        return "requests"
    if "datatransfer" in u:
        return "transfer"
    return "other"


def bucket_costs(
    db: Session, org_id: uuid.UUID, currency: str, days: int = 30
) -> dict[str, dict[str, float]]:
    """Monthly S3 cost per bucket and class (from resource-level bill lines)."""
    t = Base.metadata.tables["cost_line_items"]
    last = db.scalar(select(func.max(t.c.charge_date)).where(t.c.org_id == org_id))
    if last is None:
        return {}
    amount = fx.converted(
        t.c.effective_cost, t.c.billing_currency, t.c.charge_date, currency, t.c.org_id
    )
    rows = db.execute(
        select(t.c.resource_id, t.c.usage_type, func.sum(amount))
        .where(
            t.c.org_id == org_id,
            t.c.service_name.ilike("%simple storage service%"),
            t.c.resource_id.is_not(None),
            t.c.charge_date > last - timedelta(days=days),
        )
        .group_by(t.c.resource_id, t.c.usage_type)
    ).all()
    out: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for rid, usage, total in rows:
        out[rid][s3_class(usage)] += float(total or 0) * MONTH / days
    return out


@detector(
    "aws.s3_intelligent_tiering",
    category="storage",
    title="S3 buckets that would be cheaper on Intelligent-Tiering",
)
def s3_intelligent_tiering(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    costs = bucket_costs(db, org_id, currency)
    out = []
    for b in _resources(db, "s3_bucket"):
        a: dict[str, Any] = b.attributes or {}
        if a.get("lifecycle_rules") is None or a.get("intelligent_tiering_configs") is None:
            continue  # configuration unknown: do not guess
        if a.get("transitions") or a.get("intelligent_tiering_configs"):
            continue  # already moves data to cheaper classes
        c = costs.get(b.resource_id, {})
        standard, requests = c.get("standard", 0.0), c.get("requests", 0.0)
        if standard < IT_MIN_STANDARD or requests > standard * IT_MAX_REQUEST_SHARE:
            continue
        saving = round(standard * IT_SAVING, 2)
        out.append(
            FindingIn(
                key=b.resource_id,
                title=f"Move bucket {b.name} to S3 Intelligent-Tiering",
                description=(
                    f"Standard storage costs {standard:,.2f} {currency} a month and requests only "
                    f"{requests:,.2f}, so much of the data is rarely read. Intelligent-Tiering "
                    "moves objects not read for 30 and 90 days to cheaper tiers automatically "
                    "(40 to 68% less) with no retrieval fees. The estimate assumes 20%. Objects "
                    "under 128 KB stay in the frequent tier."
                ),
                savings_monthly=saving,
                currency=currency,
                risk="low",
                effort="low",
                overlap=b.resource_id,
                **_common(b),
                steps=[
                    "Add a lifecycle rule that transitions objects to INTELLIGENT_TIERING "
                    "after 0 days.",
                    "Check the bill a month later: the monitoring fee is 0.0025 USD per 1,000 "
                    "objects.",
                ],
                commands=[
                    f"aws s3api put-bucket-lifecycle-configuration --bucket {b.name} "
                    "--lifecycle-configuration file://intelligent-tiering.json"
                ],
                evidence={
                    "standard_monthly": round(standard, 2),
                    "requests_monthly": round(requests, 2),
                    "versioning": a.get("versioning"),
                },
            )
        )
    return out


@detector("aws.efs_lifecycle", category="storage", title="EFS without Infrequent Access")
def efs_lifecycle(db: Session, org_id: uuid.UUID, currency: str) -> list[FindingIn]:
    systems = [
        f
        for f in _resources(db, "efs_file_system")
        if (f.attributes or {}).get("transition_to_ia") is None  # known: no policy
    ]
    billed = {
        k: v * MONTH / 30
        for k, v in resource_costs(
            db, org_id, [f.resource_id for f in systems], days=30, currency=currency
        ).items()
    }
    usd = _usd(db, org_id, currency)
    out = []
    for f in systems:
        a = f.attributes or {}
        std_gb = float(a.get("bytes_standard") or a.get("bytes_total") or 0) / 1e9
        if std_gb < EFS_MIN_STANDARD_GB:
            continue
        total = float(a.get("bytes_total") or 0) / 1e9 or std_gb
        if f.resource_id in billed:
            std_cost, basis = billed[f.resource_id] * std_gb / total, "billing"
        elif usd is not None:
            std_cost, basis = std_gb * EFS_STANDARD_USD * usd, "list price"
        else:
            continue
        saving = round(std_cost * EFS_COLD_SHARE * (1 - EFS_IA_USD / EFS_STANDARD_USD), 2)
        if saving < MIN_SAVING:
            continue
        fs_id = a.get("file_system_id") or f.resource_id
        out.append(
            FindingIn(
                key=f.resource_id,
                title=f"Turn on Infrequent Access for EFS {f.name}",
                description=(
                    f"{std_gb:,.0f} GB sit in EFS Standard with no lifecycle policy. Files not "
                    "read for 30 days cost about 95% less in Infrequent Access. The estimate "
                    "assumes 30% of the data is cold; most file systems have more."
                ),
                savings_monthly=saving,
                currency=currency,
                risk="low",
                effort="low",
                overlap=f.resource_id,
                **_common(f),
                steps=[
                    "Add a lifecycle policy: to IA after 30 days, back to Standard on first "
                    "access.",
                ],
                commands=[
                    f"aws efs put-lifecycle-configuration --region {f.region} --file-system-id "
                    f"{fs_id} --lifecycle-policies TransitionToIA=AFTER_30_DAYS "
                    "TransitionToPrimaryStorageClass=AFTER_1_ACCESS"
                ],
                evidence={"standard_gb": round(std_gb, 1), "basis": basis},
            )
        )
    return out
