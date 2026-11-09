"""Demo findings: run the real detectors on the demo data once it is seeded."""

from __future__ import annotations

from datetime import UTC, datetime, time

from sqlalchemy.orm import Session

from costlens.demo.seed import DemoContext, demo_seeder


@demo_seeder("findings", order=90)
def seed_findings(db: Session, ctx: DemoContext) -> None:
    from costlens.findings.service import run

    run(db, ctx.org_id)


@demo_seeder("commitments", order=89)
def seed_commitments(db: Session, ctx: DemoContext) -> None:
    """A realistic Cost Explorer picture: 58% covered, plan well used, more to buy."""
    from datetime import UTC, datetime, time, timedelta

    from costlens.models import CommitmentSnapshot

    today = ctx.today
    db.add(
        CommitmentSnapshot(
            org_id=ctx.org_id,
            connection_id=None,
            taken_at=datetime.combine(today, time(5, 50), UTC),
            data={
                "period": {
                    "Start": (today - timedelta(days=30)).isoformat(),
                    "End": today.isoformat(),
                },
                "errors": [],
                "savings_plans": {
                    "utilization_pct": 96.4,
                    "total_commitment": 3504.0,
                    "used_commitment": 3377.9,
                    "unused_commitment": 126.1,
                    "net_savings": 1418.2,
                    "covered_spend": 3377.9,
                    "on_demand_spend": 2446.3,
                    "coverage_pct": 58.0,
                },
                "reservations": {
                    "utilization_pct": 100.0,
                    "unused_hours": 0.0,
                    "net_savings": 0.0,
                    "coverage_pct": 0.0,
                },
                "sp_recommendations": [
                    {
                        "plan_type": "COMPUTE_SP",
                        "term": "1 year",
                        "payment": "no upfront",
                        "hourly_commitment": 2.15,
                        "monthly_savings": 512.4,
                        "savings_pct": 24.6,
                        "on_demand_monthly": 2083.0,
                        "currency": "USD",
                    },
                    {
                        "plan_type": "EC2_INSTANCE_SP",
                        "term": "1 year",
                        "payment": "no upfront",
                        "hourly_commitment": 1.98,
                        "monthly_savings": 588.9,
                        "savings_pct": 30.1,
                        "on_demand_monthly": 1956.5,
                        "currency": "USD",
                    },
                ],
                "ri_recommendations": [
                    {
                        "service": "Amazon Relational Database Service",
                        "term": "1 year",
                        "payment": "no upfront",
                        "instance_type": "db.r6g.2xlarge",
                        "region": "ap-south-1",
                        "engine": "PostgreSQL",
                        "count": 2.0,
                        "monthly_savings": 431.0,
                        "savings_pct": 31.0,
                        "upfront": 0.0,
                        "currency": "USD",
                    },
                ],
            },
        )
    )
    db.flush()


@demo_seeder("aws_advice", order=88)
def seed_advice(db: Session, ctx: DemoContext) -> None:
    """AWS's own advice: one agrees with a CostLens finding, one is new."""
    from costlens.models import ExternalRecommendation

    prod = "222222222222"
    web1 = f"i-0{(int(prod) * 7919) % 16**15:015x}"
    now = datetime.combine(ctx.today, time(6, 20), UTC)
    db.add_all(
        [
            ExternalRecommendation(
                org_id=ctx.org_id,
                source="compute_optimizer",
                source_id=f"get_ec2_instance_recommendations:{web1}",
                account_id=prod,
                region="ap-south-1",
                resource_id=web1,
                resource_type="EC2 instance",
                action="rightsize",
                title="EC2 instance overprovisioned: recommended m6i.large",
                savings_monthly=2810.0,
                currency="USD",
                data={"recommended": "m6i.large"},
                fetched_at=now,
            ),
            ExternalRecommendation(
                org_id=ctx.org_id,
                source="cost_optimization_hub",
                source_id="demo-coh-lambda",
                account_id=prod,
                region="ap-south-1",
                resource_id=f"arn:aws:lambda:ap-south-1:{prod}:function:payment-webhooks",
                resource_type="LambdaFunction",
                action="rightsize",
                title="Rightsize Lambda function payment-webhooks: 1024 MB to 512 MB",
                savings_monthly=118.4,
                currency="USD",
                data={"effort": "VeryLow", "recommended": "512 MB"},
                fetched_at=now,
            ),
        ]
    )
    db.flush()


@demo_seeder("commitments_inventory", order=87)
def seed_commitment_inventory(db: Session, ctx: DemoContext) -> None:
    """A Savings Plan that ends soon, two reservations and an EDP contract."""
    from datetime import date, timedelta

    from costlens.models import Commitment, Contract

    now = datetime.combine(ctx.today, time(5, 50), UTC)

    def row(kind: str, cid: str, desc: str, ends_in: int, years: float, **kw: object) -> Commitment:
        ends = now + timedelta(days=ends_in)
        return Commitment(
            org_id=ctx.org_id,
            kind=kind,
            commitment_id=cid,
            account_id="111111111111",
            description=desc,
            starts_at=ends - timedelta(days=int(365 * years)),
            ends_at=ends,
            term_years=years,
            state="active",
            currency="USD",
            alerts_sent=[],
            seen_at=now,
            **kw,
        )

    db.add_all(
        [
            row(
                "savings_plan",
                "arn:aws:savingsplans::111111111111:savingsplan/demo-compute",
                "Compute",
                24,
                1,
                hourly_commitment=4.8,
                payment_option="No Upfront",
                utilization_pct=96.4,
                region=None,
            ),
            row(
                "ec2_ri",
                "demo-ri-m6i",
                "m6i.xlarge",
                210,
                3,
                count=4,
                upfront=6200.0,
                recurring_hourly=0.071,
                payment_option="Partial Upfront",
                offering_class="standard",
                utilization_pct=100.0,
                region="ap-south-1",
            ),
            row(
                "rds_ri",
                "demo-ri-db",
                "db.r6g.2xlarge",
                400,
                3,
                count=1,
                upfront=0.0,
                recurring_hourly=0.62,
                payment_option="No Upfront",
                utilization_pct=71.0,
                region="ap-south-1",
            ),
            Contract(
                org_id=ctx.org_id,
                name="AWS Enterprise Discount Program",
                provider="aws",
                kind="edp",
                commitment_amount=1_500_000.0,
                currency="USD",
                discount_pct=6.0,
                starts_on=date(ctx.today.year, 1, 1),
                ends_on=date(ctx.today.year + 1, 12, 31),
                notes="Two-year term; review renewal with the account team in Q3.",
                alerts_sent=[],
            ),
        ]
    )
    db.flush()
