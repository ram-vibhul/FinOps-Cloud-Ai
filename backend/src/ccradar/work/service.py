"""Open a ticket or a pull request for a finding.

- Jira (Cloud or Data Center): an issue in the chosen project, description in Atlassian
  Document Format. Auth: email + API token (basic).
- ServiceNow: a record in the chosen table (default ``incident``). Auth: user + password
  or token (basic).
- GitHub / GitLab: a branch with one Markdown fix plan under ``ccradar/fixes/`` and a
  pull (merge) request against the base branch. CloudCostRadar never changes infrastructure
  code itself: people review the plan and make the change. Auth: a token with access to
  that repository only.

Every address is checked by the SSRF guard (public HTTPS only) when saved and again
before each call. Tokens are stored encrypted and never returned."""

from __future__ import annotations

import base64
import re
from typing import Any
from urllib.parse import quote

import httpx

from ccradar.core.errors import AppError
from ccradar.core.netguard import Resolver, UnsafeURLError, check_public_https_url
from ccradar.db.base import utcnow
from ccradar.models import Finding, WorkIntegration

KINDS = ("jira", "servicenow", "github", "gitlab")
TICKETS = ("jira", "servicenow")
TIMEOUT = 20.0


class WorkError(AppError):
    status = 502
    code = "integration_failed"


def check_url(url: str, resolver: Resolver) -> str:
    try:
        return check_public_https_url(url.rstrip("/"), resolver).rstrip("/")
    except UnsafeURLError as exc:
        raise AppError(f"This address cannot be used: {exc}.", code="unsafe_url") from exc


def _money(v: Any, c: str) -> str:
    return f"{float(v):,.2f} {c}"


def summary(f: Finding) -> str:
    return f"[CloudCostRadar] {f.title}"[:250]


def plan_markdown(f: Finding, link: str) -> str:
    lines = [
        f"# {f.title}",
        "",
        f"Estimated saving: **{_money(f.savings_monthly, f.currency)} a month** "
        f"(risk {f.risk}, effort {f.effort}, confidence {f.confidence}).",
        "",
    ]
    if f.description:
        lines += [f.description, ""]
    where = [
        f"- {k}: `{v}`"
        for k, v in (
            ("Account", f.account_id),
            ("Region", f.region),
            ("Service", f.service),
            ("Resource", f.resource_id),
        )
        if v
    ]
    if where:
        lines += ["## Where", *where, ""]
    if f.steps:
        lines += ["## Steps", *[f"{i}. {s}" for i, s in enumerate(f.steps, 1)], ""]
    if f.commands:
        lines += ["## Commands (review before running)", "```sh", *map(str, f.commands), "```", ""]
    lines += [f"Opened from CloudCostRadar: {link}", ""]
    return "\n".join(lines)


def _adf(text: str) -> dict[str, Any]:
    paragraphs = [
        {"type": "paragraph", "content": [{"type": "text", "text": p}]}
        for p in text.split("\n\n")
        if p.strip()
    ]
    return {"type": "doc", "version": 1, "content": paragraphs}


def _basic(user: str | None, token: str) -> str:
    return "Basic " + base64.b64encode(f"{user or ''}:{token}".encode()).decode()


def _call(
    http: httpx.Client, method: str, url: str, resolver: Resolver, **kw: Any
) -> httpx.Response:
    check_url(url, resolver)  # DNS may have changed since the integration was saved
    try:
        r = http.request(method, url, timeout=TIMEOUT, **kw)
    except httpx.HTTPError as exc:
        raise WorkError(f"Could not reach the service: {type(exc).__name__}.") from exc
    if r.status_code >= 400:
        raise WorkError(f"The service answered {r.status_code}: {r.text[:300]}")
    return r


def _jira(
    http: httpx.Client, i: WorkIntegration, f: Finding, link: str, resolver: Resolver
) -> tuple[str, str]:
    cfg = i.config or {}
    body = {
        "fields": {
            "project": {"key": cfg.get("project_key", "")},
            "issuetype": {"name": cfg.get("issue_type") or "Task"},
            "summary": summary(f),
            "description": _adf(plan_markdown(f, link)),
            "labels": ["ccradar"],
        }
    }
    r = _call(
        http,
        "POST",
        f"{i.base_url}/rest/api/3/issue",
        resolver,
        json=body,
        headers={"Authorization": _basic(i.username, i.token), "Accept": "application/json"},
    )
    key = r.json()["key"]
    return key, f"{i.base_url}/browse/{key}"


def _servicenow(
    http: httpx.Client, i: WorkIntegration, f: Finding, link: str, resolver: Resolver
) -> tuple[str, str]:
    table = (i.config or {}).get("table") or "incident"
    r = _call(
        http,
        "POST",
        f"{i.base_url}/api/now/table/{quote(table)}",
        resolver,
        json={"short_description": summary(f), "description": plan_markdown(f, link)},
        headers={"Authorization": _basic(i.username, i.token), "Accept": "application/json"},
    )
    rec = r.json()["result"]
    target = quote(f"{table}.do?sys_id={rec['sys_id']}", safe="")
    return rec.get("number") or rec["sys_id"], f"{i.base_url}/nav_to.do?uri={target}"


def _branch(f: Finding) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", f.title.lower()).strip("-")[:40]
    return f"ccradar/{slug}-{str(f.id)[:8]}"


def _github(
    http: httpx.Client, i: WorkIntegration, f: Finding, link: str, resolver: Resolver
) -> tuple[str, str]:
    cfg = i.config or {}
    repo, base = cfg.get("repo", ""), cfg.get("base_branch") or "main"
    api = f"{i.base_url}/repos/{repo}"
    h = {"Authorization": f"Bearer {i.token}", "Accept": "application/vnd.github+json"}
    sha = _call(http, "GET", f"{api}/git/ref/heads/{quote(base)}", resolver, headers=h).json()[
        "object"
    ]["sha"]
    branch = _branch(f)
    _call(
        http,
        "POST",
        f"{api}/git/refs",
        resolver,
        headers=h,
        json={"ref": f"refs/heads/{branch}", "sha": sha},
    )
    path = f"ccradar/fixes/{str(f.id)[:8]}.md"
    _call(
        http,
        "PUT",
        f"{api}/contents/{path}",
        resolver,
        headers=h,
        json={
            "message": summary(f),
            "branch": branch,
            "content": base64.b64encode(plan_markdown(f, link).encode()).decode(),
        },
    )
    pr = _call(
        http,
        "POST",
        f"{api}/pulls",
        resolver,
        headers=h,
        json={"title": summary(f), "head": branch, "base": base, "body": plan_markdown(f, link)},
    ).json()
    return str(pr["number"]), pr["html_url"]


def _gitlab(
    http: httpx.Client, i: WorkIntegration, f: Finding, link: str, resolver: Resolver
) -> tuple[str, str]:
    cfg = i.config or {}
    project, base = quote(str(cfg.get("project", "")), safe=""), cfg.get("base_branch") or "main"
    api = f"{i.base_url}/api/v4/projects/{project}"
    h = {"PRIVATE-TOKEN": i.token}
    branch = _branch(f)
    _call(
        http,
        "POST",
        f"{api}/repository/commits",
        resolver,
        headers=h,
        json={
            "branch": branch,
            "start_branch": base,
            "commit_message": summary(f),
            "actions": [
                {
                    "action": "create",
                    "file_path": f"ccradar/fixes/{str(f.id)[:8]}.md",
                    "content": plan_markdown(f, link),
                }
            ],
        },
    )
    mr = _call(
        http,
        "POST",
        f"{api}/merge_requests",
        resolver,
        headers=h,
        json={
            "source_branch": branch,
            "target_branch": base,
            "title": summary(f),
            "description": plan_markdown(f, link),
            "remove_source_branch": True,
        },
    ).json()
    return str(mr["iid"]), mr["web_url"]


PROVIDERS = {"jira": _jira, "servicenow": _servicenow, "github": _github, "gitlab": _gitlab}


def open_item(
    http: httpx.Client, i: WorkIntegration, f: Finding, link: str, resolver: Resolver
) -> tuple[str, str]:
    """Create the ticket or pull request. Returns (external id, url)."""
    try:
        out = PROVIDERS[i.kind](http, i, f, link, resolver)
    except WorkError as exc:
        i.last_error = exc.detail[:500]
        raise
    except (KeyError, TypeError, ValueError) as exc:
        i.last_error = "Unexpected answer from the service."
        raise WorkError("The service gave an answer CloudCostRadar did not understand.") from exc
    i.last_used_at, i.last_error = utcnow(), None
    return out
