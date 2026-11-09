"""The website changelog is generated from release-notes/ (B.13)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[2] / "tools" / "changelog.py"


@pytest.fixture
def changelog():
    spec = importlib.util.spec_from_file_location("changelog_tool", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_markdown_subset_is_rendered_safely(changelog) -> None:
    out = changelog.render_note(
        [
            "Intro with `code`, **bold** and [docs](https://example.com/x).",
            "",
            "## Costs",
            "- First item",
            "  continues here",
            "- <script>alert(1)</script> and [bad](javascript:alert(1))",
        ]
    )
    assert "<p>Intro with <code>code</code>, <strong>bold</strong> and " in out
    assert '<a href="https://example.com/x">docs</a>' in out
    assert "<h3>Costs</h3>" in out and "<li>First item continues here</li>" in out
    assert "<script>" not in out and 'href="javascript' not in out


def test_releases_newest_first_and_unreleased_hidden(changelog, tmp_path, monkeypatch) -> None:
    (tmp_path / "0.9.0.md").write_text("# 0.9.0 — 2026-10-01\n\nOld.\n")
    (tmp_path / "0.10.0.md").write_text("# 0.10.0 — 2026-11-01\n\nNew.\n")
    (tmp_path / "0.11.0.md").write_text("# 0.11.0 — unreleased\n\nSoon.\n")
    monkeypatch.setattr(changelog, "NOTES", tmp_path)
    assert [v for _, v, _, _ in changelog.releases()] == ["0.10.0", "0.9.0"]  # semver order
    (tmp_path / "1.0.0.md").write_text("# 0.1.0 — 2026-11-01\n")
    with pytest.raises(SystemExit, match="first line"):
        changelog.releases()


def test_page_is_up_to_date(changelog) -> None:
    assert changelog.main(["--check"]) == 0
