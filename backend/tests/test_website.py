"""The public website (B.9): accessible pages, links that work, no third-party requests."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

SITE = Path(__file__).resolve().parents[2] / "website"
PAGES = sorted(SITE.glob("*.html"))


class Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))


def parse(path: Path) -> Page:
    page = Page()
    page.feed(path.read_text(encoding="utf-8"))
    return page


def test_all_pages_exist() -> None:
    names = {p.name for p in PAGES}
    assert {
        "index.html",
        "pricing.html",
        "getting-started.html",
        "security.html",
        "support.html",
    } <= names


@pytest.mark.parametrize("path", PAGES, ids=lambda p: p.name)
def test_page_basics(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    page = parse(path)
    tags = [t for t, _ in page.tags]
    assert re.search(r'<html lang="en">', text)
    assert tags.count("h1") == 1 and "main" in tags and "title" in tags
    assert any(t == "meta" and a.get("name") == "viewport" for t, a in page.tags)
    first_link = next(a for t, a in page.tags if t == "a")
    assert first_link.get("href") == "#main"  # skip link first
    for tag, attrs in page.tags:
        for attr in ("href", "src"):
            url = attrs.get(attr)
            if not url or url.startswith("#"):
                continue
            assert not re.match(r"(https?:)?//", url), f"{path.name}: external {url}"
            assert (SITE / url.split("#")[0]).exists(), f"{path.name}: broken link {url}"
        if tag == "img":
            assert attrs.get("alt") is not None


def test_no_prices_until_decided() -> None:
    # Q2: prices are not decided, so the site must not show any.
    for path in PAGES:
        assert not re.search(r"(\$|₹|USD|INR)\s?\d", path.read_text(encoding="utf-8")), path.name
