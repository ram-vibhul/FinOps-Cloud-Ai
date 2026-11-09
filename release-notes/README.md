# Release notes

One file per version, `MAJOR.MINOR.PATCH.md`, written for customers in plain English.
The first line is `# <version> — <YYYY-MM-DD>`, or `# <version> — unreleased` while it is
being written (unreleased notes are not published).

`tools/changelog.py` turns the released ones into `website/changelog.html` (newest
first); `make check` fails if the page is stale. Supported Markdown: `##` headings,
`-` lists, paragraphs, `code`, **bold** and [links](https://example.com).
