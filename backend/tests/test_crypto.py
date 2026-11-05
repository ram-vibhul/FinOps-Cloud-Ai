from __future__ import annotations

import os
import stat

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

from costlens.core.crypto import (
    DecryptionError,
    SecretBox,
    configure_encryption,
    local_dev_key,
    secret_box,
)
from costlens.core.netguard import UnsafeURLError, check_public_https_url
from costlens.db import Database
from costlens.db.keys import encrypted_columns, rotate_encryption_keys
from costlens.db.tenant import set_tenant
from costlens.models import OrgIdentityProvider
from tests.conftest import TEST_ENCRYPTION_KEY
from tests.helpers import make_org


def key() -> str:
    return Fernet.generate_key().decode()


def test_roundtrip_and_format() -> None:
    box = SecretBox([key()])
    token = box.encrypt("s3cret")
    assert token.startswith("v1:") and "s3cret" not in token
    assert box.decrypt(token) == "s3cret"
    assert box.encrypt("s3cret") != token  # random IV


def test_wrong_key_and_bad_format() -> None:
    token = SecretBox([key()]).encrypt("x")
    with pytest.raises(DecryptionError):
        SecretBox([key()]).decrypt(token)
    with pytest.raises(DecryptionError):
        SecretBox([key()]).decrypt("plain")


def test_rotation() -> None:
    old, new = key(), key()
    token = SecretBox([old]).encrypt("value")
    ring = SecretBox.from_setting(f"{new}, {old}")
    assert ring.decrypt(token) == "value"
    assert ring.needs_rotation(token)
    rotated = ring.rotate(token)
    assert not ring.needs_rotation(rotated)
    assert SecretBox([new]).decrypt(rotated) == "value"


def test_invalid_key_message() -> None:
    with pytest.raises(ValueError, match="generate one"):
        SecretBox(["not-a-key"])
    with pytest.raises(ValueError):
        SecretBox([])


def test_local_dev_key_file(tmp_path) -> None:
    k = local_dev_key(tmp_path)
    assert local_dev_key(tmp_path) == k
    mode = stat.S_IMODE(os.stat(tmp_path / ".encryption-key").st_mode)
    assert mode == 0o600
    SecretBox([k])


def test_encrypted_column_stored_as_ciphertext(db: Database) -> None:
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        s.add(
            OrgIdentityProvider(
                kind="oidc",
                display_name="X",
                issuer="https://idp.example",
                client_id="c",
                client_secret="top-secret",
                enabled=True,
                trust_email=False,
            )
        )
    with db.session() as s:
        set_tenant(s, bypass=True)
        raw = str(s.execute(text("SELECT client_secret FROM org_identity_providers")).scalar())
        assert raw.startswith("v1:") and "top-secret" not in raw
        row = s.query(OrgIdentityProvider).one()
        assert row.client_secret == "top-secret"


def test_rotate_keys_over_database(db: Database, admin_database_url: str) -> None:
    assert ("org_identity_providers", "id", "client_secret") in encrypted_columns()
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        s.add(
            OrgIdentityProvider(
                kind="oidc",
                display_name="X",
                issuer="https://idp.example",
                client_id="c",
                client_secret="top-secret",
                enabled=True,
                trust_email=False,
            )
        )
    old = secret_box()
    new_key = key()
    ring = SecretBox([new_key, TEST_ENCRYPTION_KEY])
    admin = Database(admin_database_url)
    try:
        first = rotate_encryption_keys(admin, ring)
        assert first["org_identity_providers.client_secret"] == 1
        assert "inbound_webhooks.secret" in first  # every encrypted column is covered
        assert rotate_encryption_keys(admin, ring)["org_identity_providers.client_secret"] == 0
    finally:
        admin.dispose()
    configure_encryption(SecretBox([new_key]))
    try:
        with db.session() as s:
            set_tenant(s, bypass=True)
            assert s.query(OrgIdentityProvider).one().client_secret == "top-secret"
    finally:
        configure_encryption(old)


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://login.example.com", True),
        ("http://login.example.com", False),
        ("https://idp.internal", False),
        ("https://127.0.0.1", False),
        ("https://169.254.169.254/latest", False),
        ("https://[::1]", False),
        ("https://[::ffff:10.0.0.1]", False),
        ("https://user:pw@login.example.com", False),
        ("https://login.example.com:8443", False),
        ("https://8.8.8.8", True),
    ],
)
def test_public_https_guard(url: str, ok: bool) -> None:
    from tests.conftest import public_resolver

    if ok:
        assert check_public_https_url(url, public_resolver) == url
    else:
        with pytest.raises(UnsafeURLError):
            check_public_https_url(url, public_resolver)
