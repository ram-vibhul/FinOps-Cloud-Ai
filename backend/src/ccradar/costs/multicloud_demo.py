"""Demo organisation beyond AWS (1.12): Azure, Google Cloud and SaaS bills, hourly costs
for the last few days, and AI usage reported by applications.

Everything is deterministic (the seeder's random generator) and ends on "today", like
the AWS demo, so the Providers, AI spend, Hourly and Early warnings views all have data.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import insert
from sqlalchemy.orm import Session

from ccradar.costs.ingest import CostLine, replace_billing_period
from ccradar.db.base import Base, utcnow
from ccradar.demo.seed import DemoContext, demo_seeder
from ccradar.models import CloudAccount

DAYS = 90
# provider, billing account, sub-account id, sub-account name, service, category, region,
# base daily USD, team, project
OTHER_CLOUDS = [
    (
        "azure",
        "demo-ea-7731",
        "4f1c2a9e-0d5b-4e7a-9a63-1d2e3f4a5b6c",
        "Corp IT (Azure)",
        "Virtual Machines",
        "Compute",
        "centralindia",
        210,
        "it",
        "intranet",
    ),
    (
        "azure",
        "demo-ea-7731",
        "4f1c2a9e-0d5b-4e7a-9a63-1d2e3f4a5b6c",
        "Corp IT (Azure)",
        "Azure SQL Database",
        "Databases",
        "centralindia",
        95,
        "it",
        "intranet",
    ),
    (
        "azure",
        "demo-ea-7731",
        "8b7d6c5e-4f3a-4b2c-9d1e-0f9e8d7c6b5a",
        "AI Lab (Azure)",
        "Azure OpenAI",
        "AI and Machine Learning",
        "eastus",
        140,
        "ml",
        "assistant",
    ),
    (
        "azure",
        "demo-ea-7731",
        "8b7d6c5e-4f3a-4b2c-9d1e-0f9e8d7c6b5a",
        "AI Lab (Azure)",
        "Storage",
        "Storage",
        "eastus",
        28,
        "ml",
        "assistant",
    ),
    (
        "gcp",
        "01A2B3-C4D5E6-F7A8B9",
        "acme-analytics",
        "acme-analytics",
        "BigQuery",
        "Analytics",
        "asia-south1",
        260,
        "data",
        "warehouse",
    ),
    (
        "gcp",
        "01A2B3-C4D5E6-F7A8B9",
        "acme-analytics",
        "acme-analytics",
        "Cloud Storage",
        "Storage",
        "asia-south1",
        44,
        "data",
        "lake",
    ),
    (
        "gcp",
        "01A2B3-C4D5E6-F7A8B9",
        "acme-ml",
        "acme-ml",
        "Vertex AI",
        "AI and Machine Learning",
        "us-central1",
        120,
        "ml",
        "recommendations",
    ),
    (
        "gcp",
        "01A2B3-C4D5E6-F7A8B9",
        "acme-ml",
        "acme-ml",
        "Compute Engine",
        "Compute",
        "us-central1",
        150,
        "ml",
        "recommendations",
    ),
]
# provider, service, category, base daily USD, team
SAAS = [
    ("datadog", "Infrastructure Hosts", "Management and Governance", 120, "platform"),
    ("datadog", "Log Management", "Management and Governance", 85, "platform"),
    ("snowflake", "Warehouse compute", "Analytics", 230, "data"),
    ("snowflake", "Storage", "Storage", 18, "data"),
    ("openai", "gpt-4.1-mini", "AI and Machine Learning", 34, "support"),
    ("github", "Actions", "Developer Tools", 22, "platform"),
    ("github", "Copilot Business", "Developer Tools", 15.8, "platform"),
]
AZURE_SUBS = {
    "4f1c2a9e-0d5b-4e7a-9a63-1d2e3f4a5b6c": "Corp IT (Azure)",
    "8b7d6c5e-4f3a-4b2c-9d1e-0f9e8d7c6b5a": "AI Lab (Azure)",
}
GCP_PROJECTS = {"acme-analytics": "acme-analytics", "acme-ml": "acme-ml"}


def _money(value: float) -> Decimal:
    return Decimal(str(round(value, 4)))


def _months(ctx: DemoContext) -> list[date]:
    month = (ctx.today - timedelta(days=DAYS - 1)).replace(day=1)
    out = []
    while month <= ctx.today:
        out.append(month)
        month = (month + timedelta(days=32)).replace(day=1)
    return out


def _days(ctx: DemoContext, month: date) -> Iterator[tuple[int, date]]:
    first = ctx.today - timedelta(days=DAYS - 1)
    for offset in range(DAYS):
        day = first + timedelta(days=offset)
        if day.replace(day=1) == month:
            yield offset, day


def _cloud_lines(ctx: DemoContext, provider: str, month: date) -> Iterator[CostLine]:
    rng = ctx.rng
    for offset, day in _days(ctx, month):
        weekend = day.weekday() >= 5
        for (
            prov,
            billing,
            sub,
            sub_name,
            service,
            category,
            region,
            base,
            team,
            project,
        ) in OTHER_CLOUDS:
            if prov != provider:
                continue
            factor = (1 + 0.001 * offset) * (1 + rng.uniform(-0.06, 0.06))
            if weekend and service in ("Virtual Machines", "Compute Engine"):
                factor *= 0.9
            if service in ("Azure OpenAI", "Vertex AI"):
                factor *= 1 + offset / 60  # AI spend growing
            cost = base * factor
            tags = {"team": team, "project": project, "env": "production"}
            yield CostLine(
                charge_date=day,
                provider=provider,
                billing_account_id=billing,
                sub_account_id=sub,
                sub_account_name=sub_name,
                service_name=service,
                service_category=category,
                region=region,
                billed_cost=_money(cost),
                effective_cost=_money(cost),
                list_cost=_money(cost),
                billing_currency="USD",
                tags=tags,
            )


def _saas_lines(ctx: DemoContext, provider: str, month: date) -> Iterator[CostLine]:
    rng = ctx.rng
    for offset, day in _days(ctx, month):
        for prov, service, category, base, team in SAAS:
            if prov != provider:
                continue
            cost = base * (1 + 0.0015 * offset) * (1 + rng.uniform(-0.08, 0.08))
            yield CostLine(
                charge_date=day,
                provider=provider,
                billing_account_id=f"demo-{provider}",
                service_name=service,
                service_category=category,
                billed_cost=_money(cost),
                effective_cost=_money(cost),
                billing_currency="USD",
                tags={"team": team},
            )


@demo_seeder("costs.other_clouds", order=24)
def seed_other_clouds(db: Session, ctx: DemoContext) -> None:
    for provider, accounts in (("azure", AZURE_SUBS), ("gcp", GCP_PROJECTS)):
        for external_id, name in accounts.items():
            db.add(
                CloudAccount(
                    org_id=ctx.org_id,
                    provider=provider,
                    external_id=external_id,
                    name=name,
                    environment="production",
                )
            )
    db.flush()
    for month in _months(ctx):
        for provider in ("azure", "gcp"):
            replace_billing_period(
                db,
                org_id=ctx.org_id,
                source_kind="demo",
                source_key=f"demo:{provider}",
                billing_period=month,
                lines=_cloud_lines(ctx, provider, month),
            )
        for provider in sorted({s[0] for s in SAAS}):
            replace_billing_period(
                db,
                org_id=ctx.org_id,
                source_kind="demo",
                source_key=f"demo:{provider}",
                billing_period=month,
                lines=_saas_lines(ctx, provider, month),
            )


HOURLY_SERVICES = {  # share of the day's AWS spend, by service
    "Amazon Elastic Compute Cloud": 0.48,
    "Amazon Relational Database Service": 0.16,
    "Amazon Redshift": 0.12,
    "Amazon Simple Storage Service": 0.08,
    "Amazon Bedrock": 0.06,
}
HOURLY_DAYS = 8


@demo_seeder("costs.hourly", order=26)
def seed_hourly(db: Session, ctx: DemoContext) -> None:
    """Hourly AWS cost for the last eight days; today runs about 30% hot on Redshift, so
    the same-day early warning has something to show."""
    rng = ctx.rng
    now = utcnow()
    last_hour = (
        now.replace(minute=0, second=0, microsecond=0)
        if ctx.today == now.date()
        else datetime.combine(ctx.today, time(23), UTC)
    )
    if last_hour.tzinfo is None:
        last_hour = last_hour.replace(tzinfo=UTC)
    daily_total = 5200.0
    rows = []
    for h in range(HOURLY_DAYS * 24):
        hour = last_hour - timedelta(hours=h)
        wave = 0.75 + 0.5 * (0.5 + 0.5 * math.sin((hour.hour - 9) / 24 * 2 * math.pi))
        for service, share in HOURLY_SERVICES.items():
            cost = daily_total * share / 24 * wave * (1 + rng.uniform(-0.04, 0.04))
            if service == "Amazon Redshift" and hour.date() == last_hour.date():
                cost *= 2.6
            rows.append(
                {
                    "org_id": ctx.org_id,
                    "hour": hour,
                    "source_key": "demo:aws",
                    "sub_account_id": "222222222222",
                    "service_name": service,
                    "billing_currency": "USD",
                    "effective_cost": _money(cost),
                }
            )
    import uuid

    for r in rows:
        r["id"] = uuid.uuid4()
    db.execute(insert(Base.metadata.tables["cost_hourly"]), rows)
    db.flush()


# app, team, feature, provider, model, calls per day, input tokens, output tokens
AI_APPS = [
    ("support-bot", "support", "answer", "anthropic", "claude-sonnet-5", 14, 180_000, 22_000),
    ("support-bot", "support", "triage", "anthropic", "claude-haiku-5-5", 20, 90_000, 4_000),
    ("search", "search", "summaries", "openai", "gpt-4.1-mini", 12, 120_000, 18_000),
    ("checkout", "payments", "fraud-notes", "bedrock", "claude-haiku-4-5", 8, 60_000, 6_000),
    ("internal-docs", "platform", "ask", "anthropic", "claude-opus-5-5", 4, 220_000, 30_000),
]


@demo_seeder("ai.usage", order=27)
def seed_ai_usage(db: Session, ctx: DemoContext) -> None:
    """30 days of AI calls as the SDK reports them (each row stands for a batch of calls)."""
    import uuid

    from ccradar.costs.ai_usage import PRICES, Event, price

    rng = ctx.rng
    rows = []
    for back in range(30):
        day = ctx.today - timedelta(days=back)
        growth = 1 + (30 - back) / 40
        for app, team, feature, provider, model, calls, tin, tout in AI_APPS:
            for i in range(calls):
                at = datetime.combine(day, time(8 + i % 12, rng.randrange(60)), UTC)
                inp = int(4 * tin * growth * rng.uniform(0.7, 1.3))
                out = int(4 * tout * growth * rng.uniform(0.7, 1.3))
                cached = int(inp * 0.4) if provider == "anthropic" else 0

                usage = Event.model_construct(
                    input_tokens=inp - cached,
                    output_tokens=out,
                    cache_read_tokens=cached,
                    cache_write_tokens=0,
                )
                cost = price(PRICES, model, usage) or 0.0
                retry = rng.random() < 0.03
                rows.append(
                    {
                        "id": uuid.uuid4(),
                        "org_id": ctx.org_id,
                        "at": at,
                        "provider": provider,
                        "model": model,
                        "app": app,
                        "team": team,
                        "feature": feature,
                        "environment": "production",
                        "input_tokens": inp - cached,
                        "output_tokens": out,
                        "cache_read_tokens": cached,
                        "cache_write_tokens": 0,
                        "cost_usd": cost,
                        "cost_source": "price list",
                        "fingerprint": f"{rng.getrandbits(64):016x}",
                        "retry": retry,
                        "status": "error" if retry else "ok",
                        "latency_ms": rng.randrange(400, 9000),
                        "source": "sdk",
                    }
                )
    db.execute(insert(Base.metadata.tables["ai_usage_events"]), rows)
    db.flush()
