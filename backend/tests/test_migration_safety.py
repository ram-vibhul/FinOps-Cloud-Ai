"""Migration safety (0.4): an upgrade must not drop or rewrite data without saying so.

Dropping tables, columns, constraints or indexes, changing a column type, or deleting rows
in ``upgrade()`` needs a ``# destructive-ok: <reason>`` comment in that function, so a
reviewer sees it and the release notes can warn self-hosted customers to back up first."""

from __future__ import annotations

import re
from pathlib import Path

VERSIONS = Path(__file__).resolve().parents[1] / "src" / "ccradar" / "migrations" / "versions"
DESTRUCTIVE = re.compile(
    r"\b(drop_table|drop_column|drop_constraint|drop_index)\b|\btype_=|"
    r"DROP\s+(TABLE|COLUMN|SCHEMA)|DELETE\s+FROM|TRUNCATE",
    re.IGNORECASE,
)


def upgrade_source(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    return text.split("def upgrade", 1)[1].split("def downgrade", 1)[0]


def test_destructive_changes_are_declared() -> None:
    files = sorted(VERSIONS.glob("*.py"))
    assert len(files) > 60
    undeclared = []
    for path in files:
        body = upgrade_source(path)
        hits = sorted({m.group(0) for m in DESTRUCTIVE.finditer(body)})
        marker = re.search(r"#\s*destructive-ok:\s*\S", body)
        if hits and not marker:
            undeclared.append(f"{path.name}: {', '.join(hits)}")
    assert not undeclared, "add '# destructive-ok: <reason>' to upgrade(): " + "; ".join(undeclared)


def test_the_check_catches_a_drop(tmp_path: Path) -> None:
    bad = tmp_path / "9999_bad.py"
    bad.write_text("def upgrade():\n    op.drop_column('t', 'c')\n\ndef downgrade():\n    pass\n")
    assert DESTRUCTIVE.search(upgrade_source(bad))
