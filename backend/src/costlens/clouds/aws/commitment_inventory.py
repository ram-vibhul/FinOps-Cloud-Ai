"""Inventory of every Savings Plan and reservation (EC2, RDS, ElastiCache, OpenSearch,
Redshift) with term, price and utilisation, plus expiry alerts for those and for
negotiated contracts entered by hand. Read-only ``Describe*`` calls; utilisation comes
from two Cost Explorer calls (about 0.01 USD each)."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from costlens.clouds.aws.client import AwsAccessError, AwsGateway
from costlens.core.notify import Message, notify
from costlens.costs.fx import converted
from costlens.db.base import utcnow
from costlens.db.tenant import set_tenant
from costlens.inventory.aws import _pages
from costlens.models import CloudConnection, Commitment, Contract, CostDaily

ALERT_DAYS = (90, 30, 7)
KIND_LABELS = {
    "savings_plan": "Savings Plan",
    "ec2_ri": "EC2 reservation",
    "rds_ri": "RDS reservation",
    "elasticache_ri": "ElastiCache reservation",
    "opensearch_ri": "OpenSearch reservation",
    "redshift_ri": "Redshift reservation",
}


@dataclass
class Item:
    kind: str
    commitment_id: str
    description: str
    region: str | None
    state: str
    starts_at: datetime | None
    ends_at: datetime | None
    count: int = 1
    hourly_commitment: float | None = None
    upfront: float | None = None
    recurring_hourly: float | None = None
    currency: str = "USD"
    term_years: float | None = None
    payment_option: str | None = None
    offering_class: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


def _dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _f(value: Any) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def _years(seconds: Any) -> float | None:
    s = _f(seconds)
    return round(s / (365 * 86400), 1) if s else None


def savings_plans(client: Any) -> Iterator[Item]:
    for page in _pages(
        client.describe_savings_plans,
        "nextToken",
        "nextToken",
        states=["active", "queued", "payment-pending"],
        maxResults=100,
    ):
        for p in page.get("savingsPlans", []):
            yield Item(
                "savings_plan",
                p.get("savingsPlanArn") or p["savingsPlanId"],
                f"{p.get('savingsPlanType')} {p.get('ec2InstanceFamily') or ''}".strip(),
                p.get("region"),
                str(p.get("state")),
                _dt(p.get("start")),
                _dt(p.get("end")),
                hourly_commitment=_f(p.get("commitment")),
                upfront=_f(p.get("upfrontPaymentAmount")),
                recurring_hourly=_f(p.get("recurringPaymentAmount")),
                currency=str(p.get("currency") or "USD"),
                term_years=_years(p.get("termDurationInSeconds")),
                payment_option=p.get("paymentOption"),
                data={"products": p.get("productTypes") or []},
            )


def _ri(
    kind: str,
    rows: list[dict[str, Any]],
    region: str,
    id_key: str,
    type_key: str,
    count_key: str,
    payment_key: str = "OfferingType",
) -> Iterator[Item]:
    for r in rows:
        start = _dt(r.get("Start") or r.get("StartTime"))
        seconds = _f(r.get("Duration"))
        end = _dt(r.get("End")) or (
            start + timedelta(seconds=seconds) if start and seconds else None
        )
        recurring = r.get("RecurringCharges") or []
        hourly = sum(_f(c.get("RecurringChargeAmount", c.get("Amount"))) or 0.0 for c in recurring)
        yield Item(
            kind,
            str(r.get(id_key)),
            str(r.get(type_key) or ""),
            region,
            str(r.get("State") or "").lower(),
            start,
            end,
            count=int(r.get(count_key) or 1),
            upfront=_f(r.get("FixedPrice")),
            recurring_hourly=hourly or None,
            currency=str(r.get("CurrencyCode") or "USD"),
            term_years=_years(seconds),
            payment_option=r.get(payment_key),
            offering_class=str(r.get("OfferingClass") or "standard").lower(),
            data={k: r.get(k) for k in ("ProductDescription", "MultiAZ", "Scope") if k in r},
        )


def ec2_reservations(client: Any, region: str) -> Iterator[Item]:
    rows = client.describe_reserved_instances(
        Filters=[{"Name": "state", "Values": ["active", "payment-pending"]}]
    ).get("ReservedInstances", [])
    yield from _ri("ec2_ri", rows, region, "ReservedInstancesId", "InstanceType", "InstanceCount")


def rds_reservations(client: Any, region: str) -> Iterator[Item]:
    rows = [
        r
        for p in _pages(client.describe_reserved_db_instances, "Marker", "Marker")
        for r in p.get("ReservedDBInstances", [])
    ]
    yield from _ri(
        "rds_ri", rows, region, "ReservedDBInstanceId", "DBInstanceClass", "DBInstanceCount"
    )


def elasticache_reservations(client: Any, region: str) -> Iterator[Item]:
    rows = [
        r
        for p in _pages(client.describe_reserved_cache_nodes, "Marker", "Marker")
        for r in p.get("ReservedCacheNodes", [])
    ]
    yield from _ri(
        "elasticache_ri", rows, region, "ReservedCacheNodeId", "CacheNodeType", "CacheNodeCount"
    )


def opensearch_reservations(client: Any, region: str) -> Iterator[Item]:
    rows = [
        r
        for p in _pages(client.describe_reserved_instances, "NextToken", "NextToken")
        for r in p.get("ReservedInstances", [])
    ]
    yield from _ri(
        "opensearch_ri",
        rows,
        region,
        "ReservedInstanceId",
        "InstanceType",
        "InstanceCount",
        "PaymentOption",
    )


def redshift_reservations(client: Any, region: str) -> Iterator[Item]:
    rows = [
        r
        for p in _pages(client.describe_reserved_nodes, "Marker", "Marker")
        for r in p.get("ReservedNodes", [])
    ]
    yield from _ri("redshift_ri", rows, region, "ReservedNodeId", "NodeType", "NodeCount")


REGIONAL: list[tuple[str, Callable[[Any, str], Iterator[Item]]]] = [
    ("ec2", ec2_reservations),
    ("rds", rds_reservations),
    ("elasticache", elasticache_reservations),
    ("opensearch", opensearch_reservations),
    ("redshift", redshift_reservations),
]


def utilisation(ce: Any, today: date) -> dict[str, float]:
    """Utilisation over the last 30 days by Savings Plan ARN and reservation id."""
    period = {"Start": (today - timedelta(days=30)).isoformat(), "End": today.isoformat()}
    out: dict[str, float] = {}
    try:
        for page in _pages(
            ce.get_savings_plans_utilization_details, "NextToken", "NextToken", TimePeriod=period
        ):
            for d in page.get("SavingsPlansUtilizationDetails", []):
                pct = _f((d.get("Utilization") or {}).get("UtilizationPercentage"))
                if d.get("SavingsPlanArn") and pct is not None:
                    out[d["SavingsPlanArn"]] = pct
    except Exception:  # noqa: S110 - utilisation is optional
        pass
    try:
        resp = ce.get_reservation_utilization(
            TimePeriod=period, GroupBy=[{"Type": "DIMENSION", "Key": "SUBSCRIPTION_ID"}]
        )
        for t in resp.get("UtilizationsByTime", []):
            for g in t.get("Groups", []):
                pct = _f((g.get("Utilization") or {}).get("UtilizationPercentage"))
                attrs = g.get("Attributes") or {}
                rid = attrs.get("reservationId") or attrs.get("leaseId") or g.get("Value")
                if rid and pct is not None:
                    out[str(rid)] = pct
    except Exception:  # noqa: S110
        pass
    return out


def _fetch(
    gateway: AwsGateway, session: Any, regions: list[str]
) -> tuple[list[Item], dict[str, str], set[str], dict[str, float]]:
    items: list[Item] = []
    errors: dict[str, str] = {}
    complete: set[str] = set()
    try:
        items += list(savings_plans(gateway.client(session, "savingsplans", region="us-east-1")))
        complete.add("savings_plan")
    except Exception as error:
        errors["savingsplans"] = type(error).__name__
    for service, fn in REGIONAL:
        ok = True
        for region in regions:
            try:
                items += list(fn(gateway.client(session, service, region=region), region))
            except Exception as error:
                errors[f"{service}:{region}"] = type(error).__name__
                ok = False
        if ok:
            complete.add(fn.__name__.split("_", 1)[0] + "_ri")
    util: dict[str, float] = {}
    try:
        util = utilisation(gateway.client(session, "ce", region="us-east-1"), utcnow().date())
    except Exception as error:
        errors["ce"] = type(error).__name__
    return items, errors, complete, util


def collect(
    db: Session, gateway: AwsGateway, connection_id: uuid.UUID, regions: list[str] | None = None
) -> dict[str, Any]:
    set_tenant(db, bypass=True)
    conn = db.get(CloudConnection, connection_id)
    if conn is None or conn.status != "active" or not conn.role_arn:
        return {}
    org_id = conn.org_id
    if regions is None:
        from costlens.inventory.service import billed_regions

        regions = billed_regions(db, org_id, conn.account_id)
    try:
        session = gateway.assume(conn.role_arn, conn.external_id, f"costlens-{org_id}")
    except AwsAccessError:
        return {"errors": {"assume": "access_denied"}}
    items, errors, complete, util = _fetch(gateway, session, regions)
    set_tenant(db, org_id=org_id)
    now = utcnow()
    existing = {
        (c.kind, c.commitment_id): c
        for c in db.scalars(select(Commitment).where(Commitment.connection_id == conn.id))
    }
    for it in items:
        row = existing.get((it.kind, it.commitment_id))
        if row is None:
            row = Commitment(
                org_id=org_id,
                connection_id=conn.id,
                kind=it.kind,
                commitment_id=it.commitment_id,
                alerts_sent=[],
            )
            db.add(row)
            existing[(it.kind, it.commitment_id)] = row
        row.account_id, row.region, row.description = (
            conn.account_id,
            it.region,
            it.description[:300],
        )
        row.count, row.hourly_commitment, row.upfront = it.count, it.hourly_commitment, it.upfront
        row.recurring_hourly, row.currency, row.term_years = (
            it.recurring_hourly,
            it.currency[:3],
            it.term_years,
        )
        row.payment_option, row.offering_class = it.payment_option, it.offering_class
        row.starts_at, row.ends_at, row.state, row.data = (
            it.starts_at,
            it.ends_at,
            it.state,
            it.data,
        )
        short = it.commitment_id.rsplit("/", 1)[-1]
        row.utilization_pct = util.get(it.commitment_id, util.get(short))
        row.seen_at = now
    seen = {(it.kind, it.commitment_id) for it in items}
    for key, row in existing.items():  # gone from AWS: it ended
        if key[0] in complete and key not in seen:
            row.state = "retired"
    db.flush()
    return {"count": len(items), "errors": errors}


def _ends(c: Commitment | Contract) -> date | None:
    if isinstance(c, Contract):
        return c.ends_on
    return c.ends_at.date() if c.ends_at else None


def expiry_alerts(db: Session, today: date | None = None) -> int:
    """Notify once per threshold (90/30/7 days) for the current organisation."""
    today = today or utcnow().date()
    messages = []
    rows: list[Commitment | Contract] = [
        *db.scalars(
            select(Commitment).where(Commitment.state.in_(("active", "queued", "payment-pending")))
        ),
        *db.scalars(select(Contract)),
    ]
    for c in rows:
        end = _ends(c)
        if end is None or end < today:
            continue
        days = (end - today).days
        due = [t for t in ALERT_DAYS if days <= t and t not in (c.alerts_sent or [])]
        if not due:
            continue
        c.alerts_sent = [*(c.alerts_sent or []), *due]
        what = (
            c.name
            if isinstance(c, Contract)
            else f"{KIND_LABELS.get(c.kind, c.kind)} {c.description}"
        )
        messages.append(
            Message(
                title=f"{what} ends in {days} days",
                text=f"Ends on {end:%d %b %Y}. Decide whether to renew, replace or let it lapse "
                "(CostLens → Savings → Commitments).",
                link="/savings/commitments",
                severity="info",
            )
        )
    db.flush()
    return notify(db, "commitment_expiring", messages) if messages else 0


def contract_progress(db: Session, contract: Contract, today: date | None = None) -> dict[str, Any]:
    """Spend so far against the commitment, the expected pace and the forecast."""
    today = today or utcnow().date()
    amount = converted(
        CostDaily.billed_cost,
        CostDaily.billing_currency,
        CostDaily.charge_date,
        contract.currency,
        CostDaily.org_id,
    )
    end = min(today, contract.ends_on)
    spent = float(
        db.scalar(
            select(func.sum(amount)).where(
                CostDaily.provider == contract.provider,
                CostDaily.charge_date >= contract.starts_on,
                CostDaily.charge_date <= end,
            )
        )
        or 0
    )
    term = max((contract.ends_on - contract.starts_on).days + 1, 1)
    elapsed = max(min((end - contract.starts_on).days + 1, term), 0)
    daily = spent / elapsed if elapsed else 0.0
    forecast = spent + daily * (term - elapsed)
    return {
        "spent": round(spent, 2),
        "progress_pct": round(100 * spent / contract.commitment_amount, 1)
        if contract.commitment_amount
        else None,
        "expected_pct": round(100 * elapsed / term, 1),
        "forecast": round(forecast, 2),
        "shortfall": round(max(contract.commitment_amount - forecast, 0.0), 2),
        "days_left": max((contract.ends_on - today).days, 0),
    }
