"""Encryption of secrets at rest (cloud credentials, IdP client secrets, tokens).

Fernet (AES-128-CBC + HMAC-SHA256) through ``MultiFernet``: the first key in
``CCRADAR_ENCRYPTION_KEYS`` encrypts, every key decrypts, so keys rotate by
prepending a new key, running ``ccradar rotate-keys``, then removing the old one.

Ciphertexts carry a ``v1:`` prefix so the format can change later.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import String
from sqlalchemy.types import TypeDecorator

log = logging.getLogger(__name__)
PREFIX = "v1:"


class DecryptionError(Exception):
    pass


class SecretBox:
    def __init__(self, keys: list[str]) -> None:
        if not keys:
            raise ValueError("at least one encryption key is required")
        try:
            self._fernets = [Fernet(k.strip().encode()) for k in keys if k.strip()]
        except ValueError as exc:
            raise ValueError(
                "invalid encryption key; generate one with "
                '`python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"`'
            ) from exc
        self._multi = MultiFernet(self._fernets)

    @classmethod
    def from_setting(cls, value: str) -> SecretBox:
        return cls([k for k in value.split(",") if k.strip()])

    def encrypt(self, plaintext: str) -> str:
        return PREFIX + self._multi.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        if not ciphertext.startswith(PREFIX):
            raise DecryptionError("unknown ciphertext format")
        try:
            return self._multi.decrypt(ciphertext[len(PREFIX) :].encode()).decode()
        except InvalidToken as exc:
            raise DecryptionError("cannot decrypt: wrong or missing key") from exc

    def needs_rotation(self, ciphertext: str) -> bool:
        """True when not encrypted with the current (first) key."""
        try:
            self._fernets[0].decrypt(ciphertext[len(PREFIX) :].encode())
        except InvalidToken:
            return True
        return False

    def rotate(self, ciphertext: str) -> str:
        return PREFIX + self._multi.rotate(ciphertext[len(PREFIX) :].encode()).decode()


_box: SecretBox | None = None


def configure_encryption(box: SecretBox) -> None:
    global _box  # noqa: PLW0603 - process-wide key ring, set once at startup
    _box = box


def secret_box() -> SecretBox:
    if _box is None:
        raise RuntimeError("encryption is not configured")
    return _box


def local_dev_key(data_dir: Path) -> str:
    """Local mode without CCRADAR_ENCRYPTION_KEYS: a per-machine key file (0600)."""
    return _local_secret(data_dir / ".encryption-key", lambda: Fernet.generate_key().decode())


def local_dev_signing_key(data_dir: Path) -> str:
    """Local mode without CCRADAR_SECRET_KEY: kept on disk so restarts keep sessions working."""
    import secrets

    return _local_secret(data_dir / ".signing-key", lambda: secrets.token_urlsafe(48))


def _local_secret(path: Path, make) -> str:  # type: ignore[no-untyped-def]
    if path.exists():
        return path.read_text().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = make()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(key)
    log.warning("generated a local development key at %s", path)
    return key


class EncryptedString(TypeDecorator[str]):
    """A string column stored encrypted. Plain ``str`` in Python, ciphertext in the DB."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: object) -> str | None:
        return None if value is None else secret_box().encrypt(value)

    def process_result_value(self, value: str | None, dialect: object) -> str | None:
        return None if value is None else secret_box().decrypt(value)
