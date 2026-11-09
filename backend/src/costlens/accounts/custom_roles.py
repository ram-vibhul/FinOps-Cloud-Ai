"""Custom roles (8.8).

A membership may carry a custom role: its permission set then replaces the built-in
role's permissions (reading is always included). A custom role may also be limited to
some cloud accounts and/or teams. Limited ("scoped") roles can use only the views that
apply the limit; every other data view refuses them, so nothing outside the scope can
leak through a view that does not know about scopes.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

from costlens.accounts.roles import Permission
from costlens.models import CustomRole, Membership

# Permissions a custom role may hold. Owner-only powers stay with owners.
GRANTABLE = (
    Permission.READ,
    Permission.WRITE,
    Permission.MANAGE_MEMBERS,
    Permission.MANAGE_CONNECTIONS,
    Permission.MANAGE_SETTINGS,
)
# What a scoped role can reach: its own session, and the views that apply the scope.
SCOPED_PATHS = re.compile(
    r"^/api/v1/(auth/(me|logout|switch)$|me/|"
    r"costs/explore$|costs/dimensions/[^/]+/values$|costs/tags$|views(/|$)|"
    r"findings$|findings/[0-9a-f-]{36}(/(history|comments|owner|links))?$)"
)


def context(
    db: Session, m: Membership | None
) -> tuple[frozenset[str] | None, dict[str, list[str]] | None]:
    """(permissions, scope) for a membership with a custom role, else (None, None)."""
    if m is None or m.custom_role_id is None:
        return None, None
    r = db.get(CustomRole, m.custom_role_id)
    if r is None:
        return None, None
    perms = frozenset(
        {
            Permission.READ.value,
            *(p for p in r.permissions or [] if p in {g.value for g in GRANTABLE}),
        }
    )
    scope = {k: list(v) for k, v in (("accounts", r.accounts or []), ("teams", r.teams or [])) if v}
    return perms, (scope or None)


def explore_filters(
    scope: dict[str, list[str]] | None, filters: dict[str, list[str]]
) -> dict[str, list[str]]:
    """Narrow report-builder filters to the scope (intersecting any the user chose)."""
    if not scope:
        return filters
    out = dict(filters)
    for dim, allowed in (("account", scope.get("accounts")), ("team", scope.get("teams"))):
        if allowed:
            asked = out.get(dim)
            out[dim] = [v for v in asked if v in allowed] if asked else list(allowed)
            if not out[dim]:
                out[dim] = ["~costlens-no-access~"]  # matches nothing (PostgreSQL refuses NUL)
    return out


def finding_visible(scope: dict[str, list[str]] | None, f: Any) -> bool:
    if not scope:
        return True
    if scope.get("accounts") and f.account_id not in scope["accounts"]:
        return False
    return not (scope.get("teams") and f.team not in scope["teams"])
