"""GitHub Actions are pinned to full commit SHAs (D.7)."""

from __future__ import annotations

import re
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"


def test_every_action_is_pinned_to_a_commit() -> None:
    uses = []
    for path in WORKFLOWS.glob("*.yml"):
        uses += re.findall(r"uses:\s*(\S+)(.*)", path.read_text())
    assert uses
    for ref, comment in uses:
        if ref.startswith("./"):
            continue  # local action
        assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", ref), ref
        assert re.search(r"#\s*v\d", comment), f"{ref}: add the version as a comment"
