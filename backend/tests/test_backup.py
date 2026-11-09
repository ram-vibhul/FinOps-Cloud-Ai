"""Backup and restore (D.8): a round trip brings data and files back exactly."""

from __future__ import annotations

import io
import json
import shutil
import tarfile
from pathlib import Path

import pytest
from sqlalchemy import select

from costlens import backup, cli
from costlens.config import Settings
from costlens.db import Database
from costlens.db.tenant import set_tenant
from costlens.models import Organization
from tests.helpers import make_org


def org_names(db: Database) -> list[str]:
    with db.session() as s:
        set_tenant(s, bypass=True)
        return sorted(s.scalars(select(Organization.name)))


def needs_tools(settings: Settings) -> None:
    if backup._kind(backup.owner_url(settings)) == "postgresql" and not shutil.which("pg_dump"):
        pytest.skip("PostgreSQL client tools not installed")


@pytest.fixture
def owner_settings(settings: Settings, admin_database_url: str) -> Settings:
    settings.migration_database_url = admin_database_url
    return settings


def test_round_trip(owner_settings: Settings, db: Database, tmp_path: Path) -> None:
    needs_tools(owner_settings)
    make_org(db, "Kept", "k@acme.com")
    files = Path(owner_settings.data_dir) / "files" / "imports"
    files.mkdir(parents=True)
    (files / "costs.csv").write_text("Date,Cost\n2026-11-01,10\n")
    out = tmp_path / "backups" / "b.tar.gz"
    manifest = backup.create(owner_settings, out)
    assert manifest["files"] == 1 and manifest["revision"] and "ENCRYPTION_KEYS" in manifest["note"]
    assert (out.stat().st_mode & 0o777) == 0o600  # it holds encrypted secrets
    assert backup.verify(out)["costlens_version"] == manifest["costlens_version"]

    make_org(db, "Added later", "l@acme.com")
    (files / "costs.csv").write_text("changed")
    (files / "new.csv").write_text("new")
    db.engine.dispose()  # restore replaces the database underneath
    backup.restore(owner_settings, out)
    assert org_names(db) == ["Kept"]
    assert (files / "costs.csv").read_text().startswith("Date,Cost")
    assert not (files / "new.csv").exists()
    kept = list(Path(owner_settings.data_dir).glob("files.before-restore-*"))
    assert len(kept) == 1 and (kept[0] / "imports" / "new.csv").exists()  # nothing lost
    safety = list(Path(owner_settings.data_dir).glob("pre-restore-*.tar.gz"))
    assert len(safety) == 1 and backup.verify(safety[0])["files"] == 2  # the state before


def _tamper(src: Path, dst: Path, manifest_changes: dict, extra: str | None = None) -> None:
    with tarfile.open(src, "r:gz") as tin, tarfile.open(dst, "w:gz") as tout:
        for m in tin.getmembers():
            data = tin.extractfile(m).read() if m.isfile() else None  # type: ignore[union-attr]
            if m.name == "manifest.json" and data is not None:
                data = json.dumps({**json.loads(data), **manifest_changes}).encode()
                m.size = len(data)
            tout.addfile(m, io.BytesIO(data) if data is not None else None)
        if extra:
            info = tarfile.TarInfo(extra)
            info.size = 1
            tout.addfile(info, io.BytesIO(b"x"))


def test_refuses_newer_and_unsafe_backups(
    owner_settings: Settings, db: Database, tmp_path: Path
) -> None:
    needs_tools(owner_settings)
    good = tmp_path / "good.tar.gz"
    backup.create(owner_settings, good)
    newer = tmp_path / "newer.tar.gz"
    _tamper(good, newer, {"revision": "9999"})
    with pytest.raises(backup.BackupError, match="newer CostLens"):
        backup.verify(newer)
    evil = tmp_path / "evil.tar.gz"
    _tamper(good, evil, {}, extra="../escape.txt")
    with pytest.raises(backup.BackupError, match="unexpected entry"):
        backup.verify(evil)
    other = tmp_path / "other.tar.gz"
    kind = (
        "sqlite" if backup._kind(backup.owner_url(owner_settings)) == "postgresql" else "postgresql"
    )
    _tamper(good, other, {"database": kind})
    with pytest.raises(backup.BackupError):
        backup.restore(owner_settings, other)
    not_backup = tmp_path / "x.tar.gz"
    with tarfile.open(not_backup, "w:gz") as t:
        info = tarfile.TarInfo("hello.txt")
        info.size = 2
        t.addfile(info, io.BytesIO(b"hi"))
    with pytest.raises(backup.BackupError, match="not a CostLens backup"):
        backup.verify(not_backup)


def test_cli(owner_settings: Settings, db: Database, tmp_path: Path, monkeypatch, capsys) -> None:
    needs_tools(owner_settings)
    monkeypatch.setattr(cli, "get_settings", lambda: owner_settings)
    out = tmp_path / "c.tar.gz"
    assert cli.main(["backup", "create", str(out)]) == 0
    captured = capsys.readouterr()
    assert "backup written" in captured.out and "ENCRYPTION_KEYS" in captured.err
    assert cli.main(["backup", "verify", str(out)]) == 0
    assert cli.main(["restore", str(out)]) == 2  # needs --yes
    assert "--yes" in capsys.readouterr().err
    assert cli.main(["backup", "verify", str(tmp_path / "missing.tar.gz")]) == 1
