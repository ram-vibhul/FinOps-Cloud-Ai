"""GitHub webhooks: deployments and releases become change events.

The customer adds ``<base url>/api/v1/hooks/github/<receiver id>`` with the receiver's
secret in their repository or organisation webhook settings. Every delivery is
verified with ``X-Hub-Signature-256`` (HMAC-SHA256 of the raw body) and de-duplicated
by deployment/release id.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime
from typing import Any

from costlens.changes.service import ChangeIn


def valid_signature(secret: str, body: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header.removeprefix("sha256="), expected)


def _when(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def to_change(event: str, payload: dict[str, Any]) -> ChangeIn | None:
    """Map a GitHub event to a change, or None if it is not interesting."""
    repo = (payload.get("repository") or {}).get("full_name", "repository")
    sender = (payload.get("sender") or {}).get("login")
    if event == "deployment_status":
        status = payload.get("deployment_status") or {}
        deployment = payload.get("deployment") or {}
        if status.get("state") != "success":
            return None
        env = deployment.get("environment")
        ref = deployment.get("ref") or deployment.get("sha", "")[:7]
        return ChangeIn(
            occurred_at=_when(status.get("created_at")),
            kind="deployment",
            title=f"{repo} deployed {ref} to {env}"[:300],
            environment=env,
            description=(deployment.get("description") or None),
            url=status.get("target_url") or deployment.get("url"),
            actor=sender,
            external_id=f"deployment:{deployment.get('id')}",
            attributes={"repository": repo, "ref": ref, "sha": deployment.get("sha")},
        )
    if event == "release" and payload.get("action") == "published":
        release = payload.get("release") or {}
        return ChangeIn(
            occurred_at=_when(release.get("published_at")),
            kind="deployment",
            title=f"{repo} released {release.get('tag_name')}"[:300],
            description=(release.get("name") or None),
            url=release.get("html_url"),
            actor=sender,
            external_id=f"release:{release.get('id')}",
            attributes={"repository": repo, "tag": release.get("tag_name")},
        )
    return None
