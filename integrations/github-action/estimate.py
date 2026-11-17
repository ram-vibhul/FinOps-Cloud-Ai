"""Run by action.yml: ask CloudCostRadar for the estimate, write the step summary and
outputs, and keep one pull-request comment up to date. Standard library only."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

MARKER = "<!-- cloudcostradar-estimate -->"


def fail(message: str) -> int:
    print(f"::error::{message}")
    return 1


def call(url: str, key: str, body: bytes, content_type: str) -> dict:
    request = urllib.request.Request(  # noqa: S310 - the customer's own https address
        url,
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": content_type},
    )
    with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
        return json.loads(response.read())


def comment(repo: str, number: str, text: str) -> None:
    body = f"{MARKER}\n{text}"
    found = subprocess.run(  # noqa: S603
        ["gh", "api", f"repos/{repo}/issues/{number}/comments", "--paginate",  # noqa: S607
         "--jq", f'.[] | select(.body | startswith("{MARKER}")) | .id'],
        capture_output=True, text=True, check=True,
    ).stdout.split()  # fmt: skip
    if found:
        args = ["gh", "api", "-X", "PATCH", f"repos/{repo}/issues/comments/{found[-1]}"]
    else:
        args = ["gh", "api", "-X", "POST", f"repos/{repo}/issues/{number}/comments"]
    subprocess.run([*args, "-f", f"body={body}"], check=True, capture_output=True)  # noqa: S603


def estimate(env: os._Environ[str] | dict[str, str]) -> dict | str:
    """The API's answer, or an error message."""
    base, key, kind = env["CCRADAR_URL"].rstrip("/"), env["CCRADAR_API_KEY"], env["KIND"]
    if not base.startswith("https://") and not base.startswith("http://localhost"):
        return "base-url must start with https://"
    if kind not in ("terraform", "kubernetes"):
        return "kind must be terraform or kubernetes"
    plan = Path(env["PLAN"])
    if not plan.is_file():
        return f"no such file: {plan}"
    try:
        if kind == "terraform":
            body = plan.read_bytes()
        else:
            before = Path(env["BEFORE"]).read_text() if env.get("BEFORE") else ""
            body = json.dumps({"after": plan.read_text(), "before": before}).encode()
        return call(f"{base}/api/v1/estimates/{kind}", key, body, "application/json")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        return f"CloudCostRadar answered {exc.code}: {detail}"
    except (urllib.error.URLError, TimeoutError) as exc:
        return f"could not reach CloudCostRadar: {exc}"


def main() -> int:
    env = os.environ
    out = estimate(env)
    if isinstance(out, str):
        return fail(out)
    change, currency = float(out["monthly_change"]), out.get("currency", "USD")
    text = out["markdown"]
    with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
        f.write(f"change={change}\ncurrency={currency}\n")
    with open(env["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
        f.write(text + "\n")
    if env.get("COMMENT") == "true" and env.get("PR_NUMBER"):
        comment(env["REPO"], env["PR_NUMBER"], text)
    if (out.get("policy") or {}).get("blocked"):  # a critical policy (11.41)
        return fail("a critical cost policy blocks this change; see the comment")
    limit = env.get("FAIL_ABOVE", "").strip()
    if limit and change > float(limit):
        return fail(f"this change adds {change:,.2f} {currency} a month (limit {limit})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
