"""Demo: one finished audit over the demo data."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ccradar.demo.seed import DemoContext, demo_seeder


@demo_seeder("aws_audit", order=95)
def seed_audit(db: Session, ctx: DemoContext) -> None:
    from ccradar.aws_audit.service import run_audit

    run_audit(db, ctx.org_id, trigger="scheduled", run_detectors=False)
