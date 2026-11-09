"""GitHub webhooks: deployments, releases and deploy workflows (GitHub Actions) become
change events. Failed deployments are kept too: they often explain an incident.

The customer adds ``<base url>/api/v1/hooks/github/<receiver id>`` with the receiver's
secret in their repository or organisation webhook settings. Every delivery is
verified with ``X-Hub-Signature-256`` (HMAC-SHA256 of the raw body) and de-duplicated
by deployment/release id.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from datetime import datetime
from typing import Any

from costlens.changes.service import ChangeIn

# Workflows whose name says they ship something.
DEPLOY_WORKFLOW = re.compile(
    r"deploy|release|rollout|ship|publish|promote|helm|argo|\bcd\b|production|staging", re.I
)


def valid_signature(secret: str, body: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header.removeprefix("sha256="), expected)


def _when(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def _deployment_status(repo: str, sender: str | None, payload: dict[str, Any]) -> ChangeIn | None:
    status = payload.get("deployment_status") or {}
    deployment = payload.get("deployment") or {}
    state = status.get("state")
    if state not in ("success", "failure", "error"):
        return None  # pending / in_progress / queued: wait for the outcome
    env = deployment.get("environment")
    ref = deployment.get("ref") or deployment.get("sha", "")[:7]
    outcome = {"failure": "failed", "error": "errored"}.get(state)
    title = (
        f"{repo} deployment of {ref} to {env} {outcome}"
        if outcome
        else f"{repo} deployed {ref} to {env}"
    )
    return ChangeIn(
        occurred_at=_when(status.get("created_at")),
        kind="deployment",
        title=title[:300],
        environment=env,
        description=(deployment.get("description") or None),
        url=status.get("target_url") or deployment.get("url"),
        actor=sender,
        external_id=f"deployment:{deployment.get('id')}",
        attributes={"repository": repo, "ref": ref, "sha": deployment.get("sha"), "status": state},
    )


def _release(repo: str, sender: str | None, payload: dict[str, Any]) -> ChangeIn | None:
    if payload.get("action") != "published":
        return None
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


def _workflow_run(repo: str, sender: str | None, payload: dict[str, Any]) -> ChangeIn | None:
    """GitHub Actions: completed runs of workflows that deploy (by name)."""
    run = payload.get("workflow_run") or {}
    name = run.get("name") or (payload.get("workflow") or {}).get("name") or ""
    conclusion = run.get("conclusion") or "unknown"
    if (
        payload.get("action") != "completed"
        or not DEPLOY_WORKFLOW.search(name)  # tests, linters and the like are not changes
        or conclusion in ("skipped", "cancelled", "neutral")
    ):
        return None
    sha = (run.get("head_sha") or "")[:7]
    branch = run.get("head_branch")
    outcome = "succeeded" if conclusion == "success" else conclusion
    actor = (run.get("triggering_actor") or run.get("actor") or {}).get("login") or sender
    return ChangeIn(
        occurred_at=_when(run.get("updated_at") or run.get("run_started_at")),
        kind="deployment",
        title=f'{repo}: workflow "{name}" {outcome} on {branch} ({sha})'[:300],
        url=run.get("html_url"),
        actor=actor,
        external_id=f"workflow_run:{run.get('id')}",
        attributes={
            "repository": repo,
            "workflow": name,
            "conclusion": conclusion,
            "sha": run.get("head_sha"),
            "branch": branch,
            "run_number": run.get("run_number"),
            "event": run.get("event"),
        },
    )


MAPPERS = {
    "deployment_status": _deployment_status,
    "release": _release,
    "workflow_run": _workflow_run,
}


def to_change(event: str, payload: dict[str, Any]) -> ChangeIn | None:
    """Map a GitHub event to a change, or None if it is not interesting."""
    mapper = MAPPERS.get(event)
    if mapper is None:
        return None
    repo = (payload.get("repository") or {}).get("full_name", "repository")
    sender = (payload.get("sender") or {}).get("login")
    return mapper(repo, sender, payload)
