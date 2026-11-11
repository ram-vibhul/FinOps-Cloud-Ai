"""Re-encrypt every ``EncryptedString`` column with the newest key."""

from __future__ import annotations

from sqlalchemy import text

from ccradar import models  # noqa: F401  (register models)
from ccradar.core.crypto import EncryptedString, SecretBox
from ccradar.db.base import Base
from ccradar.db.rls import ident
from ccradar.db.session import Database


def encrypted_columns() -> list[tuple[str, str, str]]:
    """(table, primary key column, encrypted column) for every encrypted column."""
    found = []
    for table in Base.metadata.sorted_tables:
        pk = next(iter(table.primary_key.columns)).name
        for column in table.columns:
            if isinstance(column.type, EncryptedString):
                found.append((table.name, pk, column.name))
    return found


def rotate_encryption_keys(db: Database, box: SecretBox) -> dict[str, int]:
    """Works on raw ciphertext (raw SQL, no ORM decryption) across all organisations."""
    counts: dict[str, int] = {}
    for table, pk, column in encrypted_columns():
        t, p, c = ident(table), ident(pk), ident(column)
        n = 0
        with db.engine.begin() as conn:
            if conn.dialect.name == "postgresql":
                conn.execute(text("SELECT set_config('ccradar.bypass_rls', 'on', true)"))
            rows = conn.execute(text(f"SELECT {p}, {c} FROM {t} WHERE {c} IS NOT NULL")).all()  # noqa: S608
            for row_id, value in rows:
                if box.needs_rotation(value):
                    conn.execute(
                        text(f"UPDATE {t} SET {c} = :v WHERE {p} = :id"),  # noqa: S608
                        {"v": box.rotate(value), "id": row_id},
                    )
                    n += 1
        counts[f"{table}.{column}"] = n
    return counts
