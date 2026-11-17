"""Demo organisation: the things a real team sets up over time (1.12). Budgets, policies,
virtual tags, shared-cost rules, teams, unit costs, closed months, report schedules,
saved views and a dashboard, reliability targets, finding owners and progress, a planned
commitment purchase and a saved Kubernetes investigation.

Runs after the cost, inventory, Kubernetes and findings seeders, and uses the real
services (policy evaluation, owner rules, virtual-tag recompute, month close,
investigation) so what the pages show is what the product would compute.
"""

from __future__ import annotations

import math
import zlib
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ccradar.db.base import Base
from ccradar.demo.seed import DemoContext, demo_seeder
from ccradar.models import (
    AllocationRule,
    Budget,
    Finding,
    Invoice,
    K8sCluster,
    K8sInvestigation,
    K8sMetricQuery,
    NetworkAttribution,
    OwnerRule,
    PlannedPurchase,
    Policy,
    ReliabilityTarget,
    ReportSchedule,
    SavedView,
    Team,
    UnitMetric,
    VirtualTag,
)

TEAMS = {
    "payments": "Checkout and payment services",
    "search": "Search API and indexing",
    "data": "Warehouse, lake and pipelines",
    "platform": "Shared cluster, CI and observability",
    "ml": "Recommendations and the assistant",
    "it": "Corporate IT on Azure",
    "support": "Support tooling",
}


def _first_of_month(day: date) -> date:
    return day.replace(day=1)


@demo_seeder("workspace.setup", order=92)
def seed_setup(db: Session, ctx: DemoContext) -> None:
    org_id = ctx.org_id
    for name, description in TEAMS.items():
        db.add(Team(org_id=org_id, name=name, description=description))

    # Budgets: a company budget with team budgets under it.
    company = Budget(
        org_id=org_id,
        name="Company cloud spend",
        amount=190_000,
        thresholds=[80, 100],
        recipients=["finance@demo.example"],
    )
    db.add(company)
    db.flush()
    for name, team, amount in (
        ("Payments", "payments", 62_000),
        ("Data platform", "data", 48_000),
        ("Search", "search", 19_000),
        ("Machine learning", "ml", 14_000),
    ):
        db.add(
            Budget(
                org_id=org_id,
                parent_id=company.id,
                name=name,
                amount=amount,
                filters={"tag:team": [team]},
                thresholds=[80, 100],
                recipients=[f"{team}-leads@demo.example"],
            )
        )
    db.add(
        Budget(
            org_id=org_id,
            name="AI services",
            amount=9_000,
            filters={"service": ["Amazon Bedrock", "Azure OpenAI", "Vertex AI", "gpt-4.1-mini"]},
            thresholds=[50, 80, 100],
        )
    )

    # Governance policies; violations come from the real evaluation below.
    for name, kind, params, severity in (
        ("Every resource has an owner", "required_tags", {"tags": ["team", "env"]}, "critical"),
        (
            "Only approved regions",
            "allowed_regions",
            {"regions": ["ap-south-1", "us-east-1", "centralindia", "asia-south1"]},
            "warning",
        ),
        (
            "No previous-generation instances",
            "denied_instance_types",
            {"patterns": ["r5.*", "t2.*"]},
            "warning",
        ),
        ("Containers set requests", "k8s_requests", {}, "info"),
        ("Monthly increase under $5,000", "max_monthly_increase", {"amount": 5000}, "warning"),
    ):
        db.add(Policy(org_id=org_id, name=name, kind=kind, params=params, severity=severity))

    # Virtual tags: a team for spend that has no team tag, and a cost centre.
    db.add(
        VirtualTag(
            org_id=org_id,
            key="team",
            description="Team from the account when the team tag is missing",
            default_value=None,
            rules=[
                {"value": "data", "match": {"account": ["444444444444"]}},
                {"value": "platform", "match": {"account": ["555555555555"]}},
                {"value": "platform", "match": {"provider": ["datadog", "github"]}},
            ],
        )
    )
    db.add(
        VirtualTag(
            org_id=org_id,
            key="cost_centre",
            description="Finance cost centre",
            default_value="CC-100 Shared",
            rules=[
                {"value": "CC-210 Revenue", "match": {"tag": {"team": ["payments", "search"]}}},
                {"value": "CC-320 Data and AI", "match": {"tag": {"team": ["data", "ml"]}}},
                {"value": "CC-410 Corporate", "match": {"provider": ["azure"]}},
            ],
        )
    )

    # Shared costs: tax and the shared cluster's control plane, split by team spend.
    db.add(
        AllocationRule(
            org_id=org_id,
            name="Tax and support, shared by spend",
            position=10,
            dimension="tag:team",
            filters={"service": ["Tax", "AWS Support (Business)"]},
            method="proportional",
            targets=[],
            percents={},
        )
    )
    db.add(
        AllocationRule(
            org_id=org_id,
            name="Observability, fixed split",
            position=20,
            dimension="tag:team",
            filters={"provider": ["datadog"]},
            method="fixed",
            targets=["payments", "search", "data", "platform"],
            percents={"payments": 40, "search": 20, "data": 25, "platform": 15},
        )
    )

    # Reliability targets for the important workloads.
    cluster = db.scalar(select(K8sCluster).where(K8sCluster.org_id == org_id))
    if cluster is not None:
        db.add(
            ReliabilityTarget(
                org_id=org_id,
                name="Checkout API",
                match={
                    "kind": "k8s_workload",
                    "cluster_id": str(cluster.id),
                    "namespace": "checkout",
                    "workload": "checkout-api",
                },
                tier="critical",
                availability_pct=99.95,
                headroom_pct=40,
                max_utilization_pct=65,
                min_replicas=3,
                notes="Peak sale days double traffic within minutes.",
            )
        )
    db.add(
        ReliabilityTarget(
            org_id=org_id,
            name="Monitoring namespace",
            match={"kind": "k8s_namespace", "namespace": "monitoring"},
            tier="standard",
        )
    )
    db.add(
        ReliabilityTarget(
            org_id=org_id,
            name="Sandbox",
            match={"kind": "aws_tag", "key": "env", "value": "development"},
            tier="best_effort",
            availability_pct=99.0,
            headroom_pct=0,
            max_utilization_pct=90,
        )
    )

    # Saved views and a shared dashboard built from them.
    views = []
    for name, query in (
        ("Spend by team", {"group_by": ["tag:team"], "days": 30}),
        ("Spend by provider", {"group_by": ["provider"], "days": 90}),
        ("AWS by service", {"group_by": ["service"], "days": 30, "filters": {"provider": ["aws"]}}),
        ("Spend by region", {"group_by": ["region"], "days": 30}),
    ):
        v = SavedView(org_id=org_id, name=name, kind="cost_explorer", shared=True, query=query)
        db.add(v)
        views.append(v)
    db.flush()
    db.add(
        SavedView(
            org_id=org_id,
            name="Leadership weekly",
            kind="dashboard",
            shared=True,
            query={
                "widgets": [
                    {"view_id": str(views[1].id), "title": None, "chart": "line"},
                    {"view_id": str(views[0].id), "title": None, "chart": "bar"},
                    {"view_id": str(views[2].id), "title": "Top AWS services", "chart": "table"},
                ]
            },
        )
    )

    # Scheduled reports.
    for name, kind, frequency, params in (
        ("Monthly cost summary", "cost_summary", "monthly", {}),
        ("Weekly savings", "savings", "weekly", {}),
        ("Team showback", "showback", "monthly", {"dimension": "tag:team"}),
        ("Leadership report", "leadership", "monthly", {}),
    ):
        db.add(
            ReportSchedule(
                org_id=org_id,
                name=name,
                kind=kind,
                frequency=frequency,
                recipients=["finance@demo.example"],
                params=params,
                last_sent_at=datetime.combine(ctx.today - timedelta(days=3), time(7), UTC),
            )
        )

    # Owner rules send findings to the right team.
    for priority, (kind, key, value, team) in enumerate(
        (
            ("account", "", "444444444444", "data"),
            ("account", "", "555555555555", "platform"),
            ("tag", "team", "payments", "payments"),
            ("tag", "team", "search", "search"),
            ("namespace", "", "monitoring", "platform"),
        ),
        start=1,
    ):
        db.add(
            OwnerRule(
                org_id=org_id,
                name=f"{team.title()} ({kind})",
                priority=priority * 10,
                kind=kind,
                key=key,
                value=value,
                team=team,
            )
        )

    # A commitment purchase waiting for approval.
    db.add(
        PlannedPurchase(
            org_id=org_id,
            kind="savings_plan",
            description="Compute Savings Plan, 1 year, no upfront",
            hourly_commitment=4.5,
            term="1yr",
            payment="no_upfront",
            monthly_cost=round(4.5 * 730, 2),
            estimated_monthly_saving=1180.0,
            planned_for=ctx.today + timedelta(days=9),
            status="planned",
            notes="Covers the steady production EC2 and Fargate use seen over 60 days.",
            verification=[],
        )
    )
    db.flush()


@demo_seeder("workspace.computed", order=93)
def seed_computed(db: Session, ctx: DemoContext) -> None:
    """Results the product works out from the set-up above."""
    from ccradar.costs import governance, statements, unit_economics, vtags
    from ccradar.findings import owners, workflow

    org_id = ctx.org_id
    vtags.recompute(db, org_id)
    governance.evaluate(db, org_id, "USD")
    owners.apply(db)

    # Unit costs: cost per order (checkout) and per 1,000 searches.
    first = ctx.today - timedelta(days=89)
    for name, unit, team, base, target in (
        ("Cost per order", "order", "payments", 41_000, 0.06),
        ("Cost per 1,000 searches", "1,000 searches", "search", 5_200, 0.16),
    ):
        metric = UnitMetric(
            org_id=org_id,
            name=name,
            unit=unit,
            description=f"Spend tagged team={team} divided by {unit}s.",
            filters={"tag:team": [team]},
            target_per_unit=target,
        )
        db.add(metric)
        db.flush()
        values = []
        for i in range(90):
            day = first + timedelta(days=i)
            weekly = 1.15 if day.weekday() in (4, 5) else 1.0
            values.append((day, round(base * (1 + i / 200) * weekly * ctx.rng.uniform(0.95, 1.05))))
        unit_economics.upsert_values(db, org_id, metric, values)

    # The two months before this one have invoices and are closed.
    month = _first_of_month(ctx.today)
    for back in (2, 1):
        m = month
        for _ in range(back):
            m = _first_of_month(m - timedelta(days=1))
        if m >= _first_of_month(ctx.today - timedelta(days=89)):
            _invoices(db, ctx, m)
            statements.close(db, org_id, m, None, "Closed after the invoices matched.")

    _network(db, ctx)
    _metrics(db, ctx)

    # Progress on findings: the largest few are moving through the workflow.
    # The showcase findings (Extended Support, rightsizing) stay open for visitors to see.
    showcase = ("aws.rds_extended_support", "aws.ec2_rightsizing")
    top = db.scalars(
        select(Finding)
        .where(Finding.status == "open", Finding.detector.not_in(showcase))
        .order_by(Finding.savings_monthly.desc(), Finding.dedup_key)
    ).all()
    steps = (
        ("approved", None),
        ("applied", None),
        ("verified", 0.92),
        ("approved", None),
        ("dismissed", None),
    )
    for f, (status, kept) in zip(top, steps, strict=False):
        if status in ("applied", "verified"):
            workflow.set_status(
                db, f, "approved", None, is_admin=True, note="Agreed in FinOps review"
            )
            workflow.set_status(
                db, f, "applied", None, is_admin=True, note="Done in the last change window"
            )
            if status == "verified":
                workflow.set_status(db, f, "verified", None, is_admin=True)
                f.realised_monthly = round(float(f.savings_monthly) * (kept or 1), 2)
                f.measured_at = f.verified_at
        elif status == "dismissed":
            workflow.set_status(
                db, f, "dismissed", None, is_admin=True, note="Needed for the quarter-end batch run"
            )
        else:
            workflow.set_status(db, f, status, None, is_admin=True, note="Agreed in FinOps review")
        workflow.event(db, f, "comment", None, "Owner confirmed; scheduled with the team.")
    db.flush()

    # A saved Kubernetes investigation of the failing nightly job.
    _investigation(db, ctx)


def _invoices(db: Session, ctx: DemoContext, month: date) -> None:
    """One invoice per provider and billing account, as the bill adds up (one is off by a
    credit the bill has not shown yet, so the mismatch view has an example)."""
    t = Base.metadata.tables["cost_line_items"]
    rows = db.execute(
        select(t.c.provider, t.c.billing_account_id, func.sum(t.c.billed_cost))
        .where(t.c.org_id == ctx.org_id, t.c.billing_period == month)
        .group_by(t.c.provider, t.c.billing_account_id)
    ).all()
    for provider, account, billed in rows:
        amount = float(billed or 0)
        number = zlib.crc32((account or provider).encode()) % 10_000
        if provider == "azure" and month == _first_of_month(ctx.today - timedelta(days=1)):
            amount -= 412.50
        db.add(
            Invoice(
                org_id=ctx.org_id,
                provider=provider,
                billing_account_id=account,
                month=month,
                invoice_id=f"{provider.upper()[:3]}-{month:%Y%m}-{number:04d}",
                amount=round(amount, 2),
                source="manual",
            )
        )
    db.flush()


def _network(db: Session, ctx: DemoContext) -> None:
    """Who causes transfer cost, as flow-log sampling would report it."""
    at = datetime.combine(ctx.today, time(4), UTC)
    items = [
        ("nat_processing", "checkout/checkout-api", "workload", "payments", 2.9e12, 640.0),
        ("nat_processing", "data/nightly-etl", "workload", "data", 1.7e12, 375.0),
        ("cross_az", "search/opensearch", "workload", "search", 9.8e12, 590.0),
        ("cross_az", "checkout/checkout-worker", "workload", "payments", 2.1e12, 126.0),
        ("internet_out", "web/storefront", "workload", "payments", 6.4e12, 410.0),
        ("inter_region", "i-0analytics-replica", "instance", "data", 1.4e12, 285.0),
        ("cross_az", "10.20.33.17", "ip", None, 0.6e12, 36.0),
    ]
    totals: dict[str, float] = {}
    for category, *_rest, b, _c in items:
        totals[category] = totals.get(category, 0.0) + b
    for category, key, kind, team, b, cost in items:
        db.add(
            NetworkAttribution(
                org_id=ctx.org_id,
                computed_at=at,
                category=category,
                owner_key=key,
                owner_label=key,
                owner_kind=kind,
                team=team,
                bytes=b,
                share_pct=round(100 * b / totals[category], 1),
                cost_monthly=cost,
                details={"az": "ap-south-1a"} if category == "cross_az" else {},
            )
        )
    db.flush()


def _metrics(db: Session, ctx: DemoContext) -> None:
    """A customer PromQL query with a day of points (checkout requests per second)."""
    import uuid

    from sqlalchemy import insert

    cluster = db.scalar(select(K8sCluster).where(K8sCluster.org_id == ctx.org_id))
    if cluster is None:
        return
    end = datetime.combine(ctx.today, time(0), UTC) - timedelta(hours=1)
    query = K8sMetricQuery(
        org_id=ctx.org_id,
        cluster_id=cluster.id,
        name="Checkout requests per second",
        promql='sum by (status) (rate(http_requests_total{namespace="checkout"}[5m]))',
        unit="req/s",
        last_run_at=end,
    )
    db.add(query)
    db.flush()
    rows = []
    for i in range(24 * 12):
        at = end - timedelta(minutes=5 * i)
        wave = 0.5 + 0.5 * math.sin((at.hour + at.minute / 60 - 8) / 24 * 2 * math.pi)
        for status, base in (("2xx", 180.0), ("5xx", 1.2)):
            value = base * (0.4 + wave) * ctx.rng.uniform(0.93, 1.07)
            if status == "5xx" and 18 <= i <= 30:
                value *= 9  # the error burst the investigation looks at
            rows.append(
                {
                    "id": uuid.uuid4(),
                    "org_id": ctx.org_id,
                    "cluster_id": cluster.id,
                    "query_id": query.id,
                    "at": at,
                    "series": f"status={status}",
                    "labels": {"status": status},
                    "value": round(value, 3),
                }
            )
    db.execute(insert(Base.metadata.tables["k8s_metric_points"]), rows)
    db.flush()


def _investigation(db: Session, ctx: DemoContext) -> None:
    from ccradar.k8s.investigate import gather, investigate

    cluster = db.scalar(select(K8sCluster).where(K8sCluster.org_id == ctx.org_id))
    if cluster is None:
        return
    end = datetime.combine(ctx.today, time(0), UTC) - timedelta(minutes=30)
    start = end - timedelta(hours=6)
    ev = gather(db, cluster, namespace="data", kind=None, name=None, start=start, end=end)
    result = investigate(ev, cluster.name)
    db.add(
        K8sInvestigation(
            org_id=ctx.org_id,
            cluster_id=cluster.id,
            namespace="data",
            window_start=start,
            window_end=end,
            trigger="alert",
            title=f"{result['target']}: {result['summary']}"[:300],
            result=result,
        )
    )
    db.flush()
