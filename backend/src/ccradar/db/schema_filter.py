"""Which database objects Alembic autogenerate should ignore.

* Procrastinate's job-queue tables (managed by Procrastinate).
* Monthly partitions of ``cost_line_items`` and their indexes (managed by
  ``costs/partitions.py``).
"""

from __future__ import annotations

import re
from typing import Any

_PARTITION = re.compile(r"^cost_line_items_(y\d{4}m\d{2}|default)$")


def _ignored_table(name: str | None) -> bool:
    return bool(name) and (
        str(name).startswith("procrastinate") or bool(_PARTITION.match(str(name)))
    )


def include_name(name: str | None, type_: Any, parent_names: Any) -> bool:
    if type_ == "table":
        return not _ignored_table(name)
    if type_ in ("index", "unique_constraint", "foreign_key_constraint", "column"):
        return not _ignored_table(parent_names.get("table_name"))
    return True
