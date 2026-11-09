"""Plans, entitlements and limits (B.1), data-driven.

The catalogue (plan names, features, limits) is a JSON file: the packaged
``plans.json`` holds placeholders until plans and prices are decided (tracker Q2), and
``COSTLENS_PLANS_FILE`` points at another. Prices are not here: they belong to the
payment providers (B.3, B.4).

Which plan applies:
- local mode: everything (development and trials on a laptop);
- SaaS: the organisation's plan (set by subscriptions, B.2), the trial plan while a trial
  runs, else the default plan; demo organisations get everything (they are read-only);
- self-hosted: the licence's plan (B.6), else the free tier.

Over a limit, CostLens refuses to *add* more (402 ``plan_limit``); it never deletes or
hides data that is already there, so a downgrade loses nothing."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from costlens.config import DeploymentMode, Settings
from costlens.core.errors import AppError
from costlens.models import Organization

FEATURES: dict[str, str] = {
    "kubernetes": "Kubernetes clusters",
    "ai": "AI explanations and questions in plain words",
    "api_keys": "API keys",
    "webhooks_out": "Outgoing webhooks",
    "work_integrations": "Tickets and pull requests",
    "sso": "Single sign-on",
    "scim": "User provisioning (SCIM)",
    "custom_roles": "Custom roles",
    "mcp": "AI assistant access (MCP)",
    "auto_fixes": "Automatic fixes",
    "partner": "Partner portfolio",
}
LIMITS: dict[str, str] = {
    "members": "People",
    "connections": "Cloud and SaaS connections",
    "clusters": "Kubernetes clusters",
    "cloud_spend_monthly_usd": "Cloud spend under management (USD a month)",
    "retention_days": "Longest data retention (days)",
    "api_calls_daily": "API calls a day",
    "ai_summaries_monthly": "AI summaries a month",
}
PACKAGED = Path(__file__).with_name("plans.json")


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    features: frozenset[str]
    limits: dict[str, int | None] = field(default_factory=dict)

    def allows(self, feature: str) -> bool:
        return feature in self.features

    def limit(self, key: str) -> int | None:
        return self.limits.get(key)


UNLIMITED = Plan("unlimited", "Unlimited", frozenset(FEATURES), dict.fromkeys(LIMITS))


@dataclass(frozen=True)
class Catalogue:
    plans: dict[str, Plan]
    default_plan: str
    trial_plan: str
    self_hosted_default: str
    placeholder: bool

    def get(self, plan_id: str | None) -> Plan | None:
        return self.plans.get(plan_id or "")


class CatalogueError(ValueError):
    pass


def parse(data: dict[str, Any]) -> Catalogue:
    plans: dict[str, Plan] = {}
    for raw in data.get("plans", []):
        unknown_features = set(raw.get("features", [])) - set(FEATURES)
        unknown_limits = set(raw.get("limits", {})) - set(LIMITS)
        if unknown_features or unknown_limits:
            names = ", ".join(sorted(unknown_features | unknown_limits))
            raise CatalogueError(f"plan {raw.get('id')!r}: unknown features or limits: {names}")
        limits = {k: raw.get("limits", {}).get(k) for k in LIMITS}  # missing = unlimited
        plans[raw["id"]] = Plan(raw["id"], raw["name"], frozenset(raw.get("features", [])), limits)
    cat = Catalogue(
        plans=plans,
        default_plan=data.get("default_plan", ""),
        trial_plan=data.get("trial_plan", ""),
        self_hosted_default=data.get("self_hosted_default", ""),
        placeholder=bool(data.get("placeholder")),
    )
    for role in ("default_plan", "trial_plan", "self_hosted_default"):
        if getattr(cat, role) not in plans:
            raise CatalogueError(f"{role} {getattr(cat, role)!r} is not a plan in the catalogue")
    return cat


@lru_cache(maxsize=4)
def load(path: str | None = None) -> Catalogue:
    return parse(json.loads(Path(path or PACKAGED).read_text(encoding="utf-8")))


def catalogue(settings: Settings) -> Catalogue:
    return load(settings.plans_file)


def plan_for(org: Organization, settings: Settings) -> Plan:
    if settings.deployment_mode is DeploymentMode.LOCAL or org.is_demo:
        return UNLIMITED
    cat = catalogue(settings)
    chosen = cat.get((org.settings or {}).get("plan"))  # set by hand (support, tests)
    if settings.deployment_mode is DeploymentMode.SELF_HOSTED:
        return chosen or licensed_plan(org, settings, cat) or cat.plans[cat.self_hosted_default]
    sub = _subscription(org)
    if sub is not None:
        from costlens.billing.subscriptions import plan_id_of

        return cat.get(plan_id_of(sub)) or cat.plans[cat.default_plan]
    if chosen:
        return chosen
    from costlens.accounts.trial import trial_status

    if trial_status(org)["status"] in ("active", "ending"):
        return cat.plans[cat.trial_plan]
    return cat.plans[cat.default_plan]


def licensed_plan(org: Organization, settings: Settings, cat: Catalogue) -> Plan | None:
    """The plan of a valid (or in-grace) licence, with its own limits and extra features."""
    from sqlalchemy.orm import object_session

    from costlens.billing.licence import current

    lic, _ = current(object_session(org), settings)
    if lic is None or lic.state() == "expired":
        return None
    base = cat.get(lic.plan)
    if base is None:
        return None
    return Plan(
        id=base.id,
        name=base.name,
        features=base.features | (frozenset(lic.features) & frozenset(FEATURES)),
        limits={**base.limits, **{k: v for k, v in lic.limits.items() if k in LIMITS}},
    )


def _subscription(org: Organization) -> Any:
    from sqlalchemy.orm import object_session

    from costlens.billing.subscriptions import get

    db = object_session(org)
    return get(db, org.id) if db is not None and org.id is not None else None


def require_feature(org: Organization, settings: Settings, feature: str) -> None:
    plan = plan_for(org, settings)
    if not plan.allows(feature):
        raise AppError(
            f"{FEATURES.get(feature, feature)} is not included in the {plan.name} plan.",
            status=402,
            code="plan_feature",
        )


def check_limit(
    org: Organization, settings: Settings, key: str, current: float, adding: float = 1
) -> None:
    """Refuse to go over ``key``; what is already there stays."""
    plan = plan_for(org, settings)
    limit = plan.limit(key)
    if limit is not None and current + adding > limit:
        raise AppError(
            f"The {plan.name} plan allows {limit:,} {LIMITS[key].lower()}; "
            f"you have {current:,.0f}. Remove some or choose a bigger plan.",
            status=402,
            code="plan_limit",
        )
