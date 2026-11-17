"""Help centre (11.36): user guides in docs/guides/*.md, published as website pages.

Each guide starts with ``# Title`` and a one-line summary; the rest uses the same small
Markdown subset as the release notes (``##`` headings, lists, paragraphs, `code`,
**bold**, links) plus fenced code blocks. Run without arguments to write
website/help.html and website/help-<name>.html; ``--check`` fails when they are stale
(part of ``make check``).
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from changelog import inline, render_note  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
GUIDES = ROOT / "docs/guides"
SITE = ROOT / "website"
NAME = re.compile(r"^\d{2}-([a-z0-9-]+)\.md$")
NAV = [
    ("index.html", "Product"),
    ("pricing.html", "Pricing"),
    ("getting-started.html", "Getting started"),
    ("help.html", "Help"),
    ("security.html", "Security"),
    ("support.html", "Support"),
]


def page(title: str, description: str, body: str, current: str = "help.html") -> str:
    nav = "\n".join(
        f'        <li><a href="{href}"{" aria-current=\"page\"" if href == current else ""}>{label}</a></li>'
        for href, label in NAV
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)} · CloudCostRadar</title>
  <meta name="description" content="{html.escape(description)}">
  <link rel="stylesheet" href="styles.css">
</head>
<body>
  <a class="skip" href="#main">Skip to content</a>
  <header>
    <a class="logo" href="index.html">CloudCostRadar</a>
    <nav aria-label="Main">
      <ul>
{nav}
      </ul>
    </nav>
  </header>
  <main id="main">
{body}
  </main>
  <footer>
    <p>CloudCostRadar is made by Yugiverse. <a href="security.html">Security</a> · <a href="support.html">Support</a> · <a href="changelog.html">What's new</a></p>
  </footer>
</body>
</html>
"""


def render(lines: list[str]) -> str:
    """The release-note subset, plus ``` fenced code blocks."""
    parts: list[str] = []
    chunk: list[str] = []
    code: list[str] | None = None
    for line in lines:
        if line.startswith("```"):
            if code is None:
                if chunk:
                    parts.append(render_note(chunk))
                    chunk = []
                code = []
            else:
                parts.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
                code = None
        elif code is not None:
            code.append(line)
        else:
            chunk.append(line)
    if chunk:
        parts.append(render_note(chunk))
    return "\n".join(parts).replace("<h3>", "<h2>").replace("</h3>", "</h2>")


def guides() -> list[tuple[str, str, str, list[str]]]:
    found = []
    for path in sorted(GUIDES.glob("*.md")):
        m = NAME.match(path.name)
        if not m:
            raise SystemExit(f"{path}: name guides NN-name.md")
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) < 3 or not lines[0].startswith("# ") or not lines[2].strip() == "":
            raise SystemExit(f"{path}: start with '# Title', a one-line summary, then a blank line")
        found.append((m.group(1), lines[0][2:].strip(), lines[1].strip(), lines[3:]))
    return found


def pages() -> dict[Path, str]:
    out: dict[Path, str] = {}
    items = []
    for name, title, summary, lines in guides():
        href = f"help-{name}.html"
        items.append(f'      <li><a href="{href}">{html.escape(title)}</a> — {inline(summary)}</li>')
        body = (
            f'    <p><a href="help.html">Help</a></p>\n    <h1>{html.escape(title)}</h1>\n'
            f'    <p class="lead">{inline(summary)}</p>\n{render(lines)}'
        )
        out[SITE / href] = page(title, summary, body)
    index = (
        "    <h1>Help</h1>\n"
        '    <p class="lead">Guides to getting the most out of CloudCostRadar.</p>\n'
        "    <ul>\n" + "\n".join(items) + "\n    </ul>\n"
        '    <p>Cannot find it? <a href="support.html">Ask us</a>.</p>'
    )
    out[SITE / "help.html"] = page("Help", "Guides to CloudCostRadar.", index)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    wanted = pages()
    existing = {p for p in SITE.glob("help*.html")}
    stale = [p for p, text in wanted.items() if not p.exists() or p.read_text(encoding="utf-8") != text]
    extra = existing - set(wanted)
    if args.check:
        if stale or extra:
            print("website help pages are stale: run tools/help_centre.py", file=sys.stderr)
            return 1
        return 0
    for p in extra:
        p.unlink()
    for p, text in wanted.items():
        p.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
