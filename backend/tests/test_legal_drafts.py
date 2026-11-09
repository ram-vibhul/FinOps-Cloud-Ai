"""Legal drafts (B.10) stay marked as unreviewed and unpublished until a lawyer signs off (Q6)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DRAFTS = ROOT / "legal" / "drafts"


def test_every_draft_says_it_is_not_reviewed() -> None:
    docs = {p.name for p in DRAFTS.glob("*.md")} - {"README.md"}
    assert docs == {"terms.md", "eula.md", "privacy.md", "dpa.md", "sla.md", "refund.md"}
    for name in docs:
        first = (DRAFTS / name).read_text(encoding="utf-8").splitlines()[0]
        assert "DRAFT — NOT REVIEWED BY A LAWYER. NOT FOR PUBLICATION." in first, name


def test_drafts_are_not_published() -> None:
    for page in (ROOT / "website").glob("*.html"):
        assert "legal/" not in page.read_text(encoding="utf-8"), page.name
    assert "legal" in (ROOT / ".dockerignore").read_text(encoding="utf-8")
