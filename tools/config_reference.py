#!/usr/bin/env python3
"""Write docs/configuration.md from the settings class (B.11), so the configuration
reference always matches the code. Descriptions are the comments above each field.

    backend/.venv/bin/python tools/config_reference.py          # rewrite
    backend/.venv/bin/python tools/config_reference.py --check  # fail when stale
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "backend/src/costlens/config.py"
OUTPUT = ROOT / "docs/configuration.md"
FIELD = re.compile(r"^    ([a-z_][a-z0-9_]*): ([^=]+?)(?: = (.+))?$")


def fields() -> list[tuple[str, str, str, str]]:
    """(name, type, default, description) in source order."""
    lines = SOURCE.read_text(encoding="utf-8").splitlines()
    start = next(
        i for i, line in enumerate(lines) if line.startswith("class Settings(")
    )
    out, comments = [], []
    for line in lines[start + 1 :]:
        if line and not line.startswith(" "):
            break  # end of the class
        stripped = line.strip()
        if stripped.startswith("#"):
            comments.append(stripped.lstrip("# ").strip())
            continue
        m = FIELD.match(line)
        if m and m.group(1) != "model_config":
            name, kind, default = (
                m.group(1),
                m.group(2).strip(),
                (m.group(3) or "").strip(),
            )
            trailing = ""
            if "  #" in default:
                default, trailing = (part.strip() for part in default.split("  #", 1))
            text = " ".join([*comments, trailing.lstrip("# ")]).strip()
            out.append((name, kind, default, text))
        if not stripped.startswith("#"):
            comments = []
    return out


def _default(kind: str, default: str) -> str:
    if "SecretStr" in kind:
        return "*(secret, unset)*"
    if default.startswith("Field("):
        m = re.search(r"default(?:_factory)?=([^,)]+)", default)
        default = m.group(1) if m else ""
    if default.startswith("DeploymentMode."):
        default = default.removeprefix("DeploymentMode.").lower()
    default = default.strip('"')
    if default in ("", "None"):
        return "*(unset)*"
    return "*(empty)*" if default in ("list", "[]") else f"`{default}`"


def render() -> str:
    rows = [
        f"| `COSTLENS_{name.upper()}` | {_default(kind, default)} | {text or '—'} |"
        for name, kind, default, text in fields()
    ]
    return (
        "# Configuration reference\n\n"
        "Generated from `backend/src/costlens/config.py` by `tools/config_reference.py`; do "
        "not edit by hand.\n\n"
        "Every setting is an environment variable with the `COSTLENS_` prefix (also read "
        "from a `.env` file). Secrets are never logged. Outside local mode "
        "`COSTLENS_SECRET_KEY` and `COSTLENS_ENCRYPTION_KEYS` are required.\n\n"
        "| Variable | Default | What it does |\n|---|---|---|\n"
        + "\n".join(rows)
        + "\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    text = render()
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != text:
            print(
                "docs/configuration.md is stale: run tools/config_reference.py",
                file=sys.stderr,
            )
            return 1
        return 0
    OUTPUT.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
