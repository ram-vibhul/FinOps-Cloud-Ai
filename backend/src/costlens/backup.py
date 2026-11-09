"""Backup and restore for self-hosted installations (D.8).

A backup is one ``.tar.gz`` file:

- ``manifest.json``: format, CostLens version, database kind, schema revision, time;
- ``database.sqlite`` (an online copy, safe while CostLens runs) or ``database.dump``
  (``pg_dump`` custom format, taken with the owner connection);
- ``files/``: uploaded cost files and generated reports from ``<data dir>/files``.

**Encryption keys are not in the backup** (``COSTLENS_ENCRYPTION_KEYS``,
``COSTLENS_SECRET_KEY``): keep them separately. Without the encryption keys, stored
cloud credentials and webhook addresses in a restored database cannot be read.

Restore replaces the database and the files, then runs migrations, which upgrades an
older backup to this version and re-grants the runtime role. A backup made by a newer
CostLens than the one restoring it is refused."""

from __future__ import annotations

import io
import json
import os
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import make_url

from costlens import __version__
from costlens.config import Settings

FORMAT = 1
KEY_REMINDER = (
    "This backup does not contain COSTLENS_ENCRYPTION_KEYS or COSTLENS_SECRET_KEY. "
    "Keep them separately (for example in a password manager): without the encryption "
    "keys, stored cloud credentials in a restored database cannot be read."
)


class BackupError(RuntimeError):
    pass


def owner_url(settings: Settings) -> str:
    return settings.migration_database_url or settings.database_url


def _kind(url: str) -> str:
    return "postgresql" if url.startswith(("postgresql", "postgres")) else "sqlite"


def _libpq_url(url: str) -> str:
    """The SQLAlchemy URL without its driver part, for pg_dump/pg_restore."""
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


def _tool(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise BackupError(f"{name} was not found: install the PostgreSQL 17 client tools.")
    return found


def _run(args: list[str]) -> None:
    """Run a PostgreSQL client tool (a fixed argument list, never a shell)."""
    done = subprocess.run(args, capture_output=True, text=True, check=False)  # noqa: S603
    if done.returncode != 0:
        text = (done.stderr or done.stdout or "").strip()
        hint = (
            " Use the database owner connection (COSTLENS_MIGRATION_DATABASE_URL); with"
            " Docker Compose run it through the migrate service."
            if "permission denied" in text
            else ""
        )
        raise BackupError(
            f"{Path(args[0]).name} failed: {text.splitlines()[-1] if text else ''}.{hint}"
        )


def _revision(url: str) -> str | None:
    from costlens.db.session import build_engine

    engine = build_engine(url)
    try:
        with engine.connect() as conn:
            return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    finally:
        engine.dispose()


def _known_revision(revision: str | None) -> bool:
    from alembic.script import ScriptDirectory

    from costlens.cli import alembic_config

    if revision is None:
        return True
    try:
        return (
            ScriptDirectory.from_config(alembic_config("sqlite://")).get_revision(revision)
            is not None
        )
    except Exception:  # alembic raises its own errors for unknown revisions
        return False


def _add_bytes(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size, info.mtime, info.mode = len(data), int(datetime.now(UTC).timestamp()), 0o600
    tar.addfile(info, io.BytesIO(data))


def create(settings: Settings, out: Path) -> dict[str, Any]:
    """Write a backup to ``out`` and return its manifest."""
    url = owner_url(settings)
    kind = _kind(url)
    files_dir = Path(settings.data_dir) / "files"
    with tempfile.TemporaryDirectory() as tmp:
        dump = Path(tmp) / ("database.dump" if kind == "postgresql" else "database.sqlite")
        if kind == "postgresql":
            _run(
                [
                    _tool("pg_dump"),
                    "--format=custom",
                    "--no-owner",
                    "--no-privileges",
                    f"--file={dump}",
                    f"--dbname={_libpq_url(url)}",
                ]
            )
        else:
            source = make_url(url).database
            if not source or not Path(source).exists():
                raise BackupError(f"SQLite database not found: {source}")
            src, dst = sqlite3.connect(source), sqlite3.connect(dump)
            try:
                src.backup(dst)  # consistent even while CostLens writes
            finally:
                dst.close()
                src.close()
        file_count = (
            sum(1 for p in files_dir.rglob("*") if p.is_file()) if files_dir.exists() else 0
        )
        manifest = {
            "format": FORMAT,
            "costlens_version": __version__,
            "database": kind,
            "revision": _revision(url),
            "created_at": datetime.now(UTC).isoformat(),
            "files": file_count,
            "note": KEY_REMINDER,
        }
        out.parent.mkdir(parents=True, exist_ok=True)
        partial = out.with_name(out.name + ".partial")
        fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # holds secrets
        with os.fdopen(fd, "wb") as raw, tarfile.open(fileobj=raw, mode="w:gz") as tar:
            _add_bytes(tar, "manifest.json", json.dumps(manifest, indent=2).encode())
            tar.add(dump, arcname=dump.name)
            if files_dir.exists():
                tar.add(files_dir, arcname="files")
        partial.replace(out)
    return manifest


def _members(tar: tarfile.TarFile) -> list[tarfile.TarInfo]:
    safe = []
    for m in tar.getmembers():
        parts = Path(m.name).parts
        if m.name.startswith("/") or ".." in parts or not (m.isfile() or m.isdir()):
            raise BackupError(f"unexpected entry in the backup: {m.name}")
        safe.append(m)
    return safe


def verify(path: Path) -> dict[str, Any]:
    """Read the manifest and check the database inside can be read."""
    with tarfile.open(path, "r:gz") as tar, tempfile.TemporaryDirectory() as tmp:
        names = {m.name for m in _members(tar)}
        if "manifest.json" not in names:
            raise BackupError("not a CostLens backup (no manifest.json)")
        manifest = json.loads(tar.extractfile("manifest.json").read())  # type: ignore[union-attr]
        if manifest.get("format") != FORMAT:
            raise BackupError(f"unsupported backup format {manifest.get('format')}")
        name = "database.dump" if manifest["database"] == "postgresql" else "database.sqlite"
        if name not in names:
            raise BackupError(f"the backup has no {name}")
        tar.extract(name, tmp, filter="data")
        db = Path(tmp) / name
        if manifest["database"] == "postgresql":
            _run([_tool("pg_restore"), "--list", str(db)])
        else:
            conn = sqlite3.connect(db)
            try:
                if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise BackupError("the SQLite database in the backup is damaged")
            finally:
                conn.close()
    if not _known_revision(manifest.get("revision")):
        raise BackupError(
            "this backup was made by a newer CostLens (schema "
            f"{manifest.get('revision')}); upgrade CostLens before restoring it"
        )
    return manifest


def _replace_postgres(url: str, dump: Path) -> None:
    """Empty the ``public`` schema (where all of CostLens lives) and load the dump.

    ``pg_restore --clean`` cannot drop the constraints of partitioned tables, so the
    schema is recreated instead; the caller takes a safety backup first. The owner
    connection must own the database (as in compose.yaml)."""
    from costlens.db.session import build_engine

    engine = build_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
    finally:
        engine.dispose()
    _run(
        [
            _tool("pg_restore"),
            "--no-owner",
            "--no-privileges",
            "--single-transaction",
            "--exit-on-error",
            f"--dbname={_libpq_url(url)}",
            str(dump),
        ]
    )


def restore(settings: Settings, path: Path, *, safety_copy: bool = True) -> dict[str, Any]:
    """Replace the database and files with the backup's, then migrate. First writes a
    safety backup of the current data to ``<data dir>/pre-restore-<time>.tar.gz``."""
    from costlens.cli import migrate

    manifest = verify(path)
    url = owner_url(settings)
    kind = _kind(url)
    if manifest["database"] != kind:
        raise BackupError(f"this is a {manifest['database']} backup; the installation uses {kind}")
    files_dir = Path(settings.data_dir) / "files"
    if safety_copy:
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        create(settings, Path(settings.data_dir) / f"pre-restore-{stamp}.tar.gz")
    with tarfile.open(path, "r:gz") as tar, tempfile.TemporaryDirectory() as tmp:
        members = _members(tar)
        tar.extractall(tmp, members=members, filter="data")
        if kind == "postgresql":
            _replace_postgres(url, Path(tmp) / "database.dump")
        else:
            target = Path(make_url(url).database or "")
            target.parent.mkdir(parents=True, exist_ok=True)
            staged = target.with_name(target.name + ".restoring")
            shutil.copyfile(Path(tmp) / "database.sqlite", staged)
            for extra in ("-wal", "-shm"):
                Path(f"{target}{extra}").unlink(missing_ok=True)
            staged.replace(target)
        restored = Path(tmp) / "files"
        if files_dir.exists():
            stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
            files_dir.rename(files_dir.with_name(f"files.before-restore-{stamp}"))
        if restored.exists():
            shutil.copytree(restored, files_dir)
    runtime_user = make_url(settings.database_url).username
    role = runtime_user if runtime_user and runtime_user != make_url(url).username else None
    migrate(url, app_role=role)  # upgrades an older backup and re-grants the runtime role
    return manifest
