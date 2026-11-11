"""Opt-in automatic fixes (8.6). The only code in CloudCostRadar that changes a customer's cloud.

Every guard must hold, or nothing happens:

1. **A separate role.** Fixes use their own IAM role (``CloudCostRadarFixes``), never the
   read-only role. Its policy allows only the actions below, and IAM itself refuses any
   resource tagged ``ccradar-protect = true``.
2. **Turned on by an owner, for a limited time** (at most 90 days), with a daily action
   limit and a monthly spend limit (money an action commits, such as a reservation
   bought in 8.11; deletions commit nothing).
3. **Two people.** Someone asks for a fix on a finding; a different admin approves it.
4. **Re-checked just before acting.** The resource must still be in the state that made
   it safe (volume unattached, address not associated, instance running) and must not
   carry the protect tag. Otherwise the fix fails safely.
5. **Recoverable where possible.** A volume is snapshotted before it is deleted.
6. **Audited.** Every step is a ``FixAction`` change in the audit log; the finding
   records the outcome.

Auto clean-up (8.11): when an owner turns it on, CloudCostRadar files fix requests itself for
findings that have stayed idle for a week. Those still need a person to approve them.
Approved Savings Plan purchases from the purchase plan (4.20) can also run through this
role; they count against the monthly spend limit.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ccradar.clouds.aws.client import AwsAccessError, AwsGateway
from ccradar.core.errors import AppError, ConflictError, PermissionDeniedError
from ccradar.db.base import utcnow
from ccradar.db.tenant import set_tenant
from ccradar.models import CloudConnection, Finding, FixAction

ROLE_NAME = "CloudCostRadarFixes"
PROTECT_TAG = "ccradar-protect"
MAX_DAYS = 90
ACTIONS = {
    "aws.ebs_unattached": "delete_volume",
    "aws.eip_idle": "release_address",
    "aws.ec2_rightsizing": "stop_instance",  # only for findings about idle instances
}
LABELS = {
    "delete_volume": "Snapshot, then delete the volume",
    "release_address": "Release the Elastic IP address",
    "stop_instance": "Stop the instance",
    "purchase_savings_plan": "Buy the approved Savings Plan",
}
AUTO_MIN_AGE = timedelta(days=7)
TERM_SECONDS = {"1yr": 31_536_000, "3yr": 94_608_000}
PAYMENT = {
    "no_upfront": "No Upfront",
    "partial_upfront": "Partial Upfront",
    "all_upfront": "All Upfront",
}


def policy_document() -> dict[str, Any]:
    not_protected = {"StringNotEquals": {f"aws:ResourceTag/{PROTECT_TAG}": "true"}}
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "CheckBeforeActing",
                "Effect": "Allow",
                "Action": ["ec2:DescribeVolumes", "ec2:DescribeAddresses", "ec2:DescribeInstances"],
                "Resource": "*",
            },
            {
                "Sid": "ApprovedFixesOnUnprotectedResources",
                "Effect": "Allow",
                "Action": [
                    "ec2:StopInstances",
                    "ec2:DeleteVolume",
                    "ec2:ReleaseAddress",
                    "ec2:CreateSnapshot",
                ],
                "Resource": "*",
                "Condition": not_protected,
            },
            {
                "Sid": "BuyApprovedSavingsPlans",
                "Effect": "Allow",
                "Action": [
                    "savingsplans:DescribeSavingsPlansOfferings",
                    "savingsplans:CreateSavingsPlan",
                ],
                "Resource": "*",
            },
            {
                "Sid": "TagTheSafetySnapshot",
                "Effect": "Allow",
                "Action": "ec2:CreateTags",
                "Resource": "arn:aws:ec2:*::snapshot/*",
                "Condition": {"StringEquals": {"ec2:CreateAction": "CreateSnapshot"}},
            },
        ],
    }


def cloudformation(principal_arn: str, external_id: str) -> str:
    from ccradar.clouds.aws.policy import trust_policy

    return json.dumps(
        {
            "AWSTemplateFormatVersion": "2010-09-09",
            "Description": "CloudCostRadar automatic fixes (opt-in). Separate from the read-only role.",
            "Resources": {
                "CloudCostRadarFixesRole": {
                    "Type": "AWS::IAM::Role",
                    "Properties": {
                        "RoleName": ROLE_NAME,
                        "MaxSessionDuration": 3600,
                        "AssumeRolePolicyDocument": trust_policy(principal_arn, external_id),
                        "Policies": [
                            {
                                "PolicyName": "CloudCostRadarFixes",
                                "PolicyDocument": policy_document(),
                            }
                        ],
                        "Tags": [{"Key": "managed-by", "Value": "ccradar"}],
                    },
                }
            },
            "Outputs": {"RoleArn": {"Value": {"Fn::GetAtt": ["CloudCostRadarFixesRole", "Arn"]}}},
        },
        indent=2,
    )


def settings_of(conn: CloudConnection) -> dict[str, Any] | None:
    cfg = (conn.settings or {}).get("fixes")
    if not cfg or not cfg.get("role_arn"):
        return None
    if datetime.fromisoformat(cfg["expires_at"]) <= utcnow():
        return None
    return cfg


def enable(
    db: Session,
    gateway: AwsGateway,
    conn: CloudConnection,
    *,
    role_arn: str,
    days: int,
    max_actions_per_day: int,
    spend_limit_monthly: float,
    user_id: uuid.UUID,
    auto_request: bool = False,
) -> dict[str, Any]:
    if conn.provider != "aws" or conn.status != "active":
        raise AppError("Connect this AWS account first.", code="not_active")
    if role_arn == conn.role_arn:
        raise AppError("Use the separate fixes role, not the read-only role.", code="same_role")
    if not 1 <= days <= MAX_DAYS:
        raise AppError(f"Choose 1 to {MAX_DAYS} days.", code="bad_days")
    try:
        gateway.assume(role_arn, conn.external_id, f"ccradar-fixes-check-{conn.org_id}")
    except AwsAccessError as exc:
        raise AppError(
            f"CloudCostRadar could not use the fixes role: {exc}", code="role_check_failed"
        ) from exc
    now = utcnow()
    cfg = {
        "role_arn": role_arn,
        "enabled_by": str(user_id),
        "enabled_at": now.isoformat(),
        "expires_at": (now + timedelta(days=days)).isoformat(),
        "max_actions_per_day": max_actions_per_day,
        "spend_limit_monthly": spend_limit_monthly,
        "auto_request": auto_request,
    }
    conn.settings = {**(conn.settings or {}), "fixes": cfg}
    db.flush()
    return cfg


def disable(db: Session, conn: CloudConnection) -> None:
    conn.settings = {k: v for k, v in (conn.settings or {}).items() if k != "fixes"}
    db.flush()


def connection_for(db: Session, f: Finding) -> CloudConnection:
    conns = [
        c
        for c in db.scalars(
            select(CloudConnection).where(
                CloudConnection.provider == "aws", CloudConnection.status == "active"
            )
        )
        if settings_of(c) is not None
    ]
    match = [c for c in conns if c.account_id and c.account_id == f.account_id]
    if not match:
        raise AppError(
            "Automatic fixes are not turned on for this finding's AWS account.",
            status=409,
            code="fixes_off",
        )
    return match[0]


def request(db: Session, f: Finding, user_id: uuid.UUID) -> FixAction:
    action = ACTIONS.get(f.detector)
    if action is None or (
        action == "stop_instance" and not f.dedup_key.startswith("aws.ec2_rightsizing:idle:")
    ):
        raise AppError("CloudCostRadar cannot fix this finding automatically.", code="not_fixable")
    if f.status not in ("open", "approved") or not f.resource_id or not f.region:
        raise AppError("This finding cannot be fixed now.", code="not_fixable")
    conn = connection_for(db, f)
    if db.scalar(
        select(FixAction.id).where(
            FixAction.finding_id == f.id, FixAction.status.in_(("requested", "approved", "running"))
        )
    ):
        raise ConflictError("A fix for this finding is already in progress.", code="in_progress")
    fix = FixAction(
        org_id=f.org_id,
        finding_id=f.id,
        connection_id=conn.id,
        action=action,
        resource_id=f.resource_id,
        region=f.region,
        savings_monthly=float(f.savings_monthly or 0),
        requested_by_id=user_id,
    )
    db.add(fix)
    db.flush()
    return fix


def auto_request(
    db: Session, conn: CloudConnection, now: datetime | None = None
) -> list[FixAction]:
    """File requests for idle findings in this account that have stayed for a week."""
    now = now or utcnow()
    cfg = settings_of(conn)
    if cfg is None or not cfg.get("auto_request"):
        return []
    busy = set(
        db.scalars(
            select(FixAction.finding_id).where(
                FixAction.status.in_(("requested", "approved", "running", "done"))
            )
        )
    )
    out = []
    for f in db.scalars(
        select(Finding).where(
            Finding.detector.in_(tuple(ACTIONS)),
            Finding.status == "open",
            Finding.account_id == conn.account_id,
        )
    ):
        seen = (
            f.first_seen_at
            if f.first_seen_at.tzinfo
            else f.first_seen_at.replace(tzinfo=now.tzinfo)
        )
        if f.id in busy or seen > now - AUTO_MIN_AGE:
            continue
        if f.detector == "aws.ec2_rightsizing" and not f.dedup_key.startswith(
            "aws.ec2_rightsizing:idle:"
        ):
            continue
        if not f.resource_id or not f.region:
            continue
        fix = FixAction(
            org_id=f.org_id,
            finding_id=f.id,
            connection_id=conn.id,
            action=ACTIONS[f.detector],
            resource_id=f.resource_id,
            region=f.region,
            savings_monthly=float(f.savings_monthly or 0),
            requested_by_id=None,
            result={"filed_by": "auto clean-up"},
        )
        db.add(fix)
        out.append(fix)
    db.flush()
    return out


def request_purchase(db: Session, purchase: Any, user_id: uuid.UUID) -> FixAction:
    """Ask CloudCostRadar to buy an approved Savings Plan from the purchase plan."""
    from ccradar.models import CloudConnection as Conn

    if (
        purchase.kind != "savings_plan"
        or purchase.status != "approved"
        or not purchase.hourly_commitment
    ):
        raise AppError(
            "Only approved Savings Plans in the purchase plan can be bought automatically.",
            code="not_buyable",
        )
    conns = [
        c
        for c in db.scalars(select(Conn).where(Conn.provider == "aws", Conn.status == "active"))
        if settings_of(c)
    ]
    if not conns:
        raise AppError(
            "Automatic fixes are not turned on for any AWS account.", status=409, code="fixes_off"
        )
    if db.scalar(
        select(FixAction.id).where(
            FixAction.resource_id == str(purchase.id),
            FixAction.status.in_(("requested", "approved", "running", "done")),
        )
    ):
        raise ConflictError("This purchase was already requested.", code="in_progress")
    hours = TERM_SECONDS.get(purchase.term, TERM_SECONDS["1yr"]) / 3600
    fix = FixAction(
        org_id=purchase.org_id,
        finding_id=None,
        connection_id=conns[0].id,
        action="purchase_savings_plan",
        resource_id=str(purchase.id),
        region="us-east-1",
        savings_monthly=float(purchase.estimated_monthly_saving or 0),
        commitment_cost=round(float(purchase.hourly_commitment) * hours, 2),
        requested_by_id=user_id,
    )
    db.add(fix)
    db.flush()
    return fix


def approve(db: Session, fix: FixAction, approver_id: uuid.UUID) -> FixAction:
    """Approve a fix (a different person than the requester; auto-filed ones have none)."""
    if fix.status != "requested":
        raise ConflictError("This fix was already decided.", code="already_decided")
    if approver_id == fix.requested_by_id:
        raise PermissionDeniedError("A different person must approve this fix.")
    fix.status, fix.approved_by_id = "approved", approver_id
    db.flush()
    return fix


def reject(db: Session, fix: FixAction, user_id: uuid.UUID) -> FixAction:
    if fix.status not in ("requested", "approved"):
        raise ConflictError("This fix can no longer be stopped.", code="too_late")
    fix.status = "cancelled" if user_id == fix.requested_by_id else "rejected"
    fix.result = {**(fix.result or {}), "stopped_by": str(user_id)}
    db.flush()
    return fix


def _tags(items: list[dict[str, Any]] | None) -> dict[str, str]:
    return {t.get("Key", ""): t.get("Value", "") for t in items or []}


def _check(ec2: Any, fix: FixAction) -> str | None:  # noqa: PLR0911, PLR0912 - one guard per line
    """None when the resource is still safe to change, else why not."""
    if fix.action == "delete_volume":
        vols = ec2.describe_volumes(VolumeIds=[fix.resource_id]).get("Volumes") or []
        if not vols:
            return "the volume no longer exists"
        v = vols[0]
        if _tags(v.get("Tags")).get(PROTECT_TAG) == "true":
            return f"the volume is tagged {PROTECT_TAG}"
        if v.get("State") != "available" or v.get("Attachments"):
            return "the volume is attached again"
    elif fix.action == "release_address":
        addrs = ec2.describe_addresses(AllocationIds=[fix.resource_id]).get("Addresses") or []
        if not addrs:
            return "the address no longer exists"
        a = addrs[0]
        if _tags(a.get("Tags")).get(PROTECT_TAG) == "true":
            return f"the address is tagged {PROTECT_TAG}"
        if a.get("AssociationId"):
            return "the address is in use again"
    elif fix.action == "stop_instance":
        res = ec2.describe_instances(InstanceIds=[fix.resource_id]).get("Reservations") or []
        insts = [i for r in res for i in r.get("Instances") or []]
        if not insts:
            return "the instance no longer exists"
        i = insts[0]
        if _tags(i.get("Tags")).get(PROTECT_TAG) == "true":
            return f"the instance is tagged {PROTECT_TAG}"
        if (i.get("State") or {}).get("Name") != "running":
            return "the instance is not running"
        if any(t.get("Key") == "aws:autoscaling:groupName" for t in i.get("Tags") or []):
            return "the instance belongs to an Auto Scaling group"
    return None


def _buy(db: Session, sp: Any, fix: FixAction) -> tuple[str | None, dict[str, Any]]:
    """(reason not to, outcome) for a Savings Plan purchase."""
    from ccradar.models import PlannedPurchase

    p = db.get(PlannedPurchase, uuid.UUID(fix.resource_id))
    if p is None or p.status != "approved":
        return "the purchase is no longer approved in the plan", {}
    offers = (
        sp.describe_savings_plans_offerings(
            planTypes=["Compute"],
            durations=[TERM_SECONDS.get(p.term, TERM_SECONDS["1yr"])],
            paymentOptions=[PAYMENT.get(p.payment, "No Upfront")],
            currencies=["USD"],
        ).get("searchResults")
        or []
    )
    if not offers:
        return "AWS offers no matching Savings Plan", {}
    out = sp.create_savings_plan(
        savingsPlanOfferingId=offers[0]["offeringId"],
        commitment=f"{p.hourly_commitment:.3f}",
        clientToken=str(fix.id),  # never buys twice, even if retried
        tags={"created-by": "ccradar"},
    )
    p.status = "purchased"
    return None, {
        "savings_plan_id": out.get("savingsPlanId"),
        "offering_id": offers[0]["offeringId"],
    }


def _act(ec2: Any, fix: FixAction) -> dict[str, Any]:
    if fix.action == "delete_volume":
        snap = ec2.create_snapshot(
            VolumeId=fix.resource_id,
            Description=f"CloudCostRadar safety snapshot before deleting {fix.resource_id}",
            TagSpecifications=[
                {
                    "ResourceType": "snapshot",
                    "Tags": [
                        {"Key": "created-by", "Value": "ccradar"},
                        {"Key": "ccradar-fix", "Value": str(fix.id)},
                    ],
                }
            ],
        )
        ec2.delete_volume(VolumeId=fix.resource_id)
        return {"snapshot_id": snap.get("SnapshotId")}
    if fix.action == "release_address":
        ec2.release_address(AllocationId=fix.resource_id)
        return {}
    ec2.stop_instances(InstanceIds=[fix.resource_id])
    return {}


def _fail(db: Session, fix: FixAction, reason: str) -> FixAction:
    fix.status, fix.executed_at = "failed", utcnow()
    fix.result = {**(fix.result or {}), "error": reason}
    db.flush()
    return fix


def execute(db: Session, gateway: AwsGateway, fix_id: uuid.UUID) -> FixAction | None:  # noqa: PLR0911 - each guard stops here
    set_tenant(db, bypass=True)
    fix = db.get(FixAction, fix_id)
    if fix is None or fix.status != "approved":
        return fix
    set_tenant(db, org_id=fix.org_id)
    conn = db.get(CloudConnection, fix.connection_id)
    cfg = settings_of(conn) if conn else None
    if conn is None or cfg is None:
        return _fail(db, fix, "automatic fixes were turned off or expired")
    now = utcnow()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    done_today = (
        db.scalar(
            select(func.count())
            .select_from(FixAction)
            .where(
                FixAction.connection_id == conn.id,
                FixAction.status == "done",
                FixAction.executed_at >= day_start,
            )
        )
        or 0
    )
    if done_today >= int(cfg["max_actions_per_day"]):
        return _fail(db, fix, "the daily limit of automatic fixes was reached")
    month_start = day_start.replace(day=1)
    spent = (
        db.scalar(
            select(func.coalesce(func.sum(FixAction.commitment_cost), 0.0)).where(
                FixAction.connection_id == conn.id,
                FixAction.status == "done",
                FixAction.executed_at >= month_start,
            )
        )
        or 0.0
    )
    if float(spent) + fix.commitment_cost > float(cfg["spend_limit_monthly"]):
        return _fail(db, fix, "the monthly spend limit for automatic fixes would be exceeded")
    fix.status = "running"
    db.flush()
    try:
        session = gateway.assume(cfg["role_arn"], conn.external_id, f"ccradar-fix-{fix.id}")
        if fix.action == "purchase_savings_plan":
            why_not, outcome = _buy(
                db, gateway.client(session, "savingsplans", region="us-east-1"), fix
            )
            if why_not:
                return _fail(db, fix, f"not bought: {why_not}")
        else:
            ec2 = gateway.client(session, "ec2", region=fix.region)
            why_not = _check(ec2, fix)
            if why_not:
                return _fail(db, fix, f"not changed: {why_not}")
            outcome = _act(ec2, fix)
    except Exception as exc:  # any AWS error: record it, change nothing more
        return _fail(db, fix, f"{type(exc).__name__}: {str(exc)[:300]}")
    fix.status, fix.executed_at = "done", utcnow()
    fix.result = {**(fix.result or {}), **outcome}
    f = db.get(Finding, fix.finding_id) if fix.finding_id else None
    if f is not None:
        from ccradar.findings.workflow import event, set_status

        if f.status in ("open", "approved"):
            set_status(
                db,
                f,
                "applied",
                None,
                is_admin=True,
                note=f"Fixed automatically: {LABELS[fix.action]}.",
            )
        event(
            db,
            f,
            "comment",
            None,
            f"Automatic fix done: {LABELS[fix.action]}"
            + (f" (snapshot {outcome['snapshot_id']})" if outcome.get("snapshot_id") else "")
            + ".",
        )
    db.flush()
    return fix
