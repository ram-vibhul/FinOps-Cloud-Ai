"""Demo: run anomaly detection over the seeded costs (finds the Redshift spike)."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from costlens.costs.anomalies import detect
from costlens.demo.seed import DemoContext, demo_seeder


@demo_seeder("costs.anomalies", order=30)
def seed_anomalies(db: Session, ctx: DemoContext) -> None:
    for back in range(6, -1, -1):
        detect(db, ctx.org_id, through=ctx.today - timedelta(days=back), recent_days=1)
