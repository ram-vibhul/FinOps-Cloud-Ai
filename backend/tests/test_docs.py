"""Documentation (B.11): every guide exists and relative links resolve."""

from __future__ import annotations

import re
from pathlib import Path

DOCS = Path(__file__).resolve().parents[2] / "docs"


def test_guides_exist() -> None:
    names = {p.name for p in DOCS.glob("*.md")}
    assert {
        "README.md",
        "architecture.md",
        "installation.md",
        "configuration.md",
        "security.md",
        "api.md",
        "releasing.md",
        "backup.md",
    } <= names


def test_relative_links_resolve() -> None:
    for page in DOCS.glob("*.md"):
        for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", page.read_text(encoding="utf-8")):
            if "://" in target:
                continue
            assert (page.parent / target).exists(), f"{page.name}: broken link {target}"


def test_every_setting_is_documented() -> None:
    from costlens.config import Settings

    text = (DOCS / "configuration.md").read_text(encoding="utf-8")
    for name in Settings.model_fields:
        assert f"`COSTLENS_{name.upper()}`" in text, name
    assert "| — |" not in text  # every setting has a description
