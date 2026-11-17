"""Encrypted backups and object storage (11.6, 11.7)."""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Any

import pytest

from ccradar import backup, backup_crypto, cli
from ccradar.config import Settings
from ccradar.core.storage import S3Storage, build_storage
from ccradar.db import Database
from tests.helpers import make_org

PASS = "correct horse battery staple"


class FakeS3:
    """Just enough of boto3's S3 client."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.extra: dict[tuple[str, str], dict] = {}

    def put_object(self, Bucket: str, Key: str, Body: bytes, **kw: Any) -> None:
        self.objects[(Bucket, Key)] = Body
        self.extra[(Bucket, Key)] = kw

    def get_object(self, Bucket: str, Key: str) -> dict:
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def delete_object(self, Bucket: str, Key: str) -> None:
        self.objects.pop((Bucket, Key), None)

    def head_object(self, Bucket: str, Key: str) -> dict:
        if (Bucket, Key) not in self.objects:
            err = Exception("404")
            err.response = {"Error": {"Code": "404"}}  # type: ignore[attr-defined]
            raise err
        return {}

    def upload_file(self, path: str, bucket: str, key: str, ExtraArgs: dict | None = None) -> None:
        self.objects[(bucket, key)] = Path(path).read_bytes()
        self.extra[(bucket, key)] = ExtraArgs or {}

    def download_file(self, bucket: str, key: str, path: str) -> None:
        Path(path).write_bytes(self.objects[(bucket, key)])


def roundtrip(data: bytes, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> bytes:
    monkeypatch.setattr(backup_crypto, "CHUNK", 1000)  # several chunks
    src, enc = tmp_path / "in", tmp_path / "enc"
    src.write_bytes(data)
    with enc.open("wb") as f:
        backup_crypto.encrypt_file(src, f, PASS)
    out = io.BytesIO()
    backup_crypto.decrypt_file(enc, out, PASS)
    return out.getvalue()


def test_encryption_round_trip_and_tampering(tmp_path: Path, monkeypatch) -> None:
    data = os.urandom(3500)
    assert roundtrip(data, tmp_path, monkeypatch) == data
    assert roundtrip(b"", tmp_path, monkeypatch) == b""
    enc = tmp_path / "enc"
    raw = enc.read_bytes()
    assert data[:200] not in raw
    with pytest.raises(backup_crypto.BackupCryptoError, match="wrong passphrase"):
        backup_crypto.decrypt_file(enc, io.BytesIO(), "not the passphrase")
    # Cut short after whole chunks: refused, not half-restored.
    roundtrip(data, tmp_path, monkeypatch)
    raw = enc.read_bytes()
    first_len = int.from_bytes(raw[24:28], "big")
    enc.write_bytes(raw[: 24 + 4 + 12 + first_len])
    with pytest.raises(backup_crypto.BackupCryptoError, match="cut short"):
        backup_crypto.decrypt_file(enc, io.BytesIO(), PASS)
    # One flipped byte anywhere: refused.
    roundtrip(data, tmp_path, monkeypatch)
    raw = bytearray(enc.read_bytes())
    raw[-5] ^= 1
    enc.write_bytes(bytes(raw))
    with pytest.raises(backup_crypto.BackupCryptoError):
        backup_crypto.decrypt_file(enc, io.BytesIO(), PASS)


def test_passphrase_rules(monkeypatch) -> None:
    monkeypatch.delenv(backup_crypto.PASSPHRASE_ENV, raising=False)
    assert backup_crypto.passphrase_from_env(False) is None
    with pytest.raises(backup_crypto.BackupCryptoError, match="set CCRADAR_BACKUP_PASSPHRASE"):
        backup_crypto.passphrase_from_env(True)
    monkeypatch.setenv(backup_crypto.PASSPHRASE_ENV, "short")
    with pytest.raises(backup_crypto.BackupCryptoError, match="at least 12"):
        backup_crypto.passphrase_from_env(True)


@pytest.fixture
def owner_settings(settings: Settings, admin_database_url: str) -> Settings:
    settings.migration_database_url = admin_database_url
    return settings


def test_encrypted_backup_to_object_storage(owner_settings: Settings, db: Database) -> None:
    import shutil

    if backup._kind(backup.owner_url(owner_settings)) == "postgresql" and not shutil.which(
        "pg_dump"
    ):
        pytest.skip("PostgreSQL client tools not installed")
    make_org(db, "Kept", "k@acme.com")
    s3 = FakeS3()
    where = "s3://acme-backups/ccradar/nightly.tar.gz.enc"
    manifest = backup.create(owner_settings, where, passphrase=PASS, client=s3)
    assert manifest["encrypted"] is True
    stored = s3.objects[("acme-backups", "ccradar/nightly.tar.gz.enc")]
    assert stored.startswith(backup_crypto.MAGIC) and b"Kept" not in stored
    assert s3.extra[("acme-backups", "ccradar/nightly.tar.gz.enc")] == {
        "ServerSideEncryption": "AES256"
    }
    assert (
        backup.verify(where, passphrase=PASS, settings=owner_settings, client=s3)["encrypted"]
        is True
    )
    with pytest.raises(backup.BackupError, match="encrypted"):
        backup.verify(where, settings=owner_settings, client=s3)
    with pytest.raises(backup.BackupError, match="wrong passphrase"):
        backup.verify(
            where, passphrase="another long passphrase", settings=owner_settings, client=s3
        )


def test_cli_encrypts_only_with_a_passphrase(
    owner_settings, db, tmp_path, monkeypatch, capsys
) -> None:
    import shutil

    if backup._kind(backup.owner_url(owner_settings)) == "postgresql" and not shutil.which(
        "pg_dump"
    ):
        pytest.skip("PostgreSQL client tools not installed")
    monkeypatch.setattr(cli, "get_settings", lambda: owner_settings)
    monkeypatch.delenv(backup_crypto.PASSPHRASE_ENV, raising=False)
    out = tmp_path / "b.enc"
    assert cli.main(["backup", "create", str(out), "--encrypt"]) == 1
    assert "CCRADAR_BACKUP_PASSPHRASE" in capsys.readouterr().err
    monkeypatch.setenv(backup_crypto.PASSPHRASE_ENV, PASS)
    assert cli.main(["backup", "create", str(out), "--encrypt"]) == 0
    assert "encrypted" in capsys.readouterr().out
    assert backup_crypto.is_encrypted(out)
    assert cli.main(["backup", "verify", str(out)]) == 0
    monkeypatch.delenv(backup_crypto.PASSPHRASE_ENV)
    assert cli.main(["backup", "verify", str(out)]) == 1


def test_s3_storage(tmp_path: Path) -> None:
    s3 = FakeS3()
    store = S3Storage("s3://files-bucket/prod/", client=s3)
    store.put("imports/a.csv", b"x,y")
    assert s3.objects[("files-bucket", "prod/imports/a.csv")] == b"x,y"
    assert s3.extra[("files-bucket", "prod/imports/a.csv")] == {"ServerSideEncryption": "AES256"}
    assert store.get("imports/a.csv") == b"x,y" and store.exists("imports/a.csv")
    store.delete("imports/a.csv")
    assert not store.exists("imports/a.csv")
    with pytest.raises(ValueError, match="unsafe"):
        store.put("../escape", b"")
    with pytest.raises(ValueError, match="s3://"):
        S3Storage("https://bucket", client=s3)
    assert type(build_storage(str(tmp_path))).__name__ == "LocalStorage"


def test_storage_url_setting_is_checked() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(_env_file=None, storage_url="http://not-s3")  # type: ignore[call-arg]
    assert Settings(_env_file=None, storage_url="s3://b/p").storage_url == "s3://b/p"  # type: ignore[call-arg]
