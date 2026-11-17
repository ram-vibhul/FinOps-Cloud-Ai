"""Encrypted backups (11.6) and backups kept in object storage.

Format: ``CCRBAK1\\n`` + 16-byte salt + chunks. Each chunk is
``length (4 bytes) | nonce (12) | AES-256-GCM ciphertext`` of up to 4 MB of the backup.
The key comes from the passphrase with scrypt. Each chunk authenticates its position and
whether it is the last one, so a cut-short, reordered or altered file is refused instead
of half-restored. The passphrase is read from ``CCRADAR_BACKUP_PASSPHRASE``, never from the
command line (it would show in the process list and shell history)."""

from __future__ import annotations

import os
import struct
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"CCRBAK1\n"
CHUNK = 4 * 1024 * 1024
PASSPHRASE_ENV = "CCRADAR_BACKUP_PASSPHRASE"  # noqa: S105 - the variable name, not a secret
MIN_PASSPHRASE = 12


class BackupCryptoError(ValueError):
    pass


def _key(passphrase: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode())


def _aad(index: int, last: bool) -> bytes:
    return struct.pack(">Q?", index, last)


def passphrase_from_env(required: bool) -> str | None:
    value = os.environ.get(PASSPHRASE_ENV) or None
    if required and not value:
        raise BackupCryptoError(f"set {PASSPHRASE_ENV} to the backup passphrase")
    if value is not None and len(value) < MIN_PASSPHRASE:
        raise BackupCryptoError(
            f"the backup passphrase must be at least {MIN_PASSPHRASE} characters"
        )
    return value


def is_encrypted(path: Path) -> bool:
    with path.open("rb") as f:
        return f.read(len(MAGIC)) == MAGIC


def encrypt_file(source: Path, target: BinaryIO, passphrase: str) -> None:
    salt = os.urandom(16)
    aead = AESGCM(_key(passphrase, salt))
    target.write(MAGIC + salt)
    size = source.stat().st_size
    with source.open("rb") as f:
        index, done = 0, 0
        while True:
            block = f.read(CHUNK)
            done += len(block)
            last = done >= size
            nonce = os.urandom(12)
            sealed = aead.encrypt(nonce, block, _aad(index, last))
            target.write(struct.pack(">I", len(sealed)) + nonce + sealed)
            index += 1
            if last:
                return


def decrypt_file(source: Path, target: BinaryIO, passphrase: str) -> None:
    with source.open("rb") as f:
        if f.read(len(MAGIC)) != MAGIC:
            raise BackupCryptoError("not an encrypted CloudCostRadar backup")
        aead = AESGCM(_key(passphrase, f.read(16)))
        index = 0
        while True:
            header = f.read(4)
            if len(header) < 4:
                raise BackupCryptoError("the encrypted backup is cut short")
            (length,) = struct.unpack(">I", header)
            nonce, sealed = f.read(12), f.read(length)
            for last in (False, True):
                try:
                    target.write(aead.decrypt(nonce, sealed, _aad(index, last)))
                    break
                except InvalidTag:
                    continue
            else:
                raise BackupCryptoError("wrong passphrase, or the backup was altered")
            index += 1
            if last:
                if f.read(1):
                    raise BackupCryptoError("unexpected data after the end of the backup")
                return


# --- object storage ------------------------------------------------------------------------


def is_remote(location: str) -> bool:
    return location.startswith("s3://")


def _split(location: str) -> tuple[str, str]:
    bucket, _, key = location.removeprefix("s3://").partition("/")
    if not bucket or not key:
        raise BackupCryptoError("use s3://bucket/path/to/backup.tar.gz")
    return bucket, key


def _client(endpoint_url: str | None, region: str | None, client: Any = None) -> Any:
    if client is not None:
        return client
    import boto3

    return boto3.client("s3", endpoint_url=endpoint_url, region_name=region)


def upload(path: Path, location: str, *, endpoint_url: str | None = None,
           region: str | None = None, client: Any = None) -> None:  # fmt: skip
    bucket, key = _split(location)
    _client(endpoint_url, region, client).upload_file(
        str(path), bucket, key, ExtraArgs={"ServerSideEncryption": "AES256"}
    )


@contextmanager
def local_copy(location: str, *, endpoint_url: str | None = None, region: str | None = None,
               client: Any = None) -> Iterator[Path]:  # fmt: skip
    """A local path for ``location``: the file itself, or a downloaded temporary copy."""
    if not is_remote(location):
        yield Path(location)
        return
    bucket, key = _split(location)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "backup"
        _client(endpoint_url, region, client).download_file(bucket, key, str(path))
        yield path
