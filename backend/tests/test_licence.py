from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
from pydantic import SecretStr

from costlens import cli
from costlens.billing import licence, plans
from costlens.config import DeploymentMode, Settings
from costlens.db import Database
from costlens.db.tenant import set_tenant
from costlens.models import User
from tests.helpers import make_org, sign_in_client

NOW = datetime(2026, 11, 9, tzinfo=UTC)


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def public(key: Ed25519PrivateKey) -> str:
    return b64(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))


def sign(key: Ed25519PrivateKey, **fields) -> str:
    payload = {
        "v": 1,
        "id": "lic-1",
        "customer": "Acme Ltd",
        "plan": "business",
        "issued_at": NOW.isoformat(),
        "expires_at": (NOW + timedelta(days=365)).isoformat(),
        "grace_days": 14,
        **fields,
    }
    body = json.dumps(payload).encode()
    return f"CLv1.{b64(body)}.{b64(key.sign(body))}"


@pytest.fixture
def yugiverse(monkeypatch: pytest.MonkeyPatch) -> Ed25519PrivateKey:
    key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(licence, "PUBLIC_KEYS", (public(key),))
    return key


def test_signature_and_format(yugiverse: Ed25519PrivateKey) -> None:
    lic = licence.decode(sign(yugiverse, limits={"members": 250}, features=["partner"]))
    assert (lic.customer, lic.plan, lic.limits, lic.features) == (
        "Acme Ltd",
        "business",
        {"members": 250},
        ("partner",),
    )
    forged = sign(Ed25519PrivateKey.generate())
    with pytest.raises(licence.LicenceError, match="not issued by Yugiverse"):
        licence.decode(forged)
    head, body, sig = sign(yugiverse).split(".")
    tampered = json.loads(base64.urlsafe_b64decode(body + "=="))
    tampered["plan"] = "enterprise"
    with pytest.raises(licence.LicenceError):
        licence.decode(f"{head}.{b64(json.dumps(tampered).encode())}.{sig}")
    with pytest.raises(licence.LicenceError, match="not a CostLens licence"):
        licence.decode("hello")
    with pytest.raises(licence.LicenceError, match="newer version"):
        licence.decode(sign(yugiverse, v=2))


def test_no_built_in_key_means_no_licence_verifies() -> None:
    key = Ed25519PrivateKey.generate()
    assert licence.PUBLIC_KEYS == ()  # production key pair: tracker Q14
    with pytest.raises(licence.LicenceError):
        licence.decode(sign(key))


def test_rotated_keys_still_verify(monkeypatch: pytest.MonkeyPatch) -> None:
    old, new = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    monkeypatch.setattr(licence, "PUBLIC_KEYS", (public(new), public(old)))
    assert licence.decode(sign(old)).plan == "business"


def test_states_and_grace(yugiverse: Ed25519PrivateKey) -> None:
    lic = licence.decode(sign(yugiverse))
    assert lic.state(NOW) == "valid"
    assert lic.state(NOW + timedelta(days=370)) == "grace"
    assert lic.state(NOW + timedelta(days=380)) == "expired"


def test_plan_follows_the_licence(
    yugiverse: Ed25519PrivateKey, settings: Settings, db: Database, tmp_path: Path
) -> None:
    settings.deployment_mode = DeploymentMode.SELF_HOSTED
    org_id, _ = make_org(db, "Acme", "o@acme.com")
    from costlens.models import Organization

    def plan_now():
        with db.session() as s:
            set_tenant(s, org_id=org_id)
            org = s.get(Organization, org_id)
            assert org is not None
            return plans.plan_for(org, settings)

    assert plan_now().id == "free"  # no licence: free tier
    future = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    settings.licence_key = SecretStr(
        sign(yugiverse, expires_at=future, limits={"members": 250}, features=["partner", "warp"])
    )
    p = plan_now()
    assert p.id == "business" and p.limit("members") == 250 and p.allows("partner")
    assert not p.allows("warp")  # unknown features are ignored
    in_grace = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    settings.licence_key = SecretStr(sign(yugiverse, expires_at=in_grace))
    assert plan_now().id == "business"  # grace: still working
    lapsed = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    settings.licence_key = SecretStr(sign(yugiverse, expires_at=lapsed))
    assert plan_now().id == "free"
    settings.licence_key = None
    path = tmp_path / "licence.txt"
    path.write_text(sign(yugiverse, expires_at=future, plan="team"))
    settings.licence_file = str(path)
    assert plan_now().id == "team"


def test_install_through_the_api(
    yugiverse: Ed25519PrivateKey, client: TestClient, db: Database
) -> None:
    client.app.state.settings.deployment_mode = DeploymentMode.SELF_HOSTED  # type: ignore[attr-defined]
    org_id, owner = make_org(db, "Acme", "o@acme.com")
    c = sign_in_client(client, db, owner, org_id)
    assert c.get("/api/v1/system/licence").json()["state"] == "none"
    future = (datetime.now(UTC) + timedelta(days=60)).isoformat()
    key = sign(yugiverse, expires_at=future)
    assert c.put("/api/v1/system/licence", json={"key": key}).status_code == 403
    with db.session() as s:
        set_tenant(s, bypass=True)
        u = s.get(User, owner)
        assert u is not None
        u.is_platform_admin = True
    bad = c.put("/api/v1/system/licence", json={"key": sign(Ed25519PrivateKey.generate())})
    assert bad.status_code == 422 and bad.json()["code"] == "bad_licence"
    out = c.put("/api/v1/system/licence", json={"key": key}).json()
    assert out["state"] == "valid" and out["customer"] == "Acme Ltd" and out["days_left"] >= 59
    assert c.get("/api/v1/org/plan").json()["plan"]["id"] == "business"
    assert c.get("/api/v1/auth/me").json()["user"]["is_platform_admin"] is True


def test_cli(
    yugiverse: Ed25519PrivateKey, settings: Settings, db: Database, tmp_path, capsys, monkeypatch
) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    make_org(db, "Acme", "o@acme.com")
    path = tmp_path / "licence.txt"
    path.write_text(sign(yugiverse, expires_at=(datetime.now(UTC) + timedelta(days=9)).isoformat()))
    assert cli.main(["licence", "install", str(path)]) == 0
    assert '"state": "valid"' in capsys.readouterr().out
    assert cli.main(["platform-admin", "add", "O@acme.com"]) == 0
    assert cli.main(["platform-admin", "list"]) == 0
    assert "o@acme.com" in capsys.readouterr().out
    assert cli.main(["platform-admin", "add", "nobody@x.com"]) == 1
    assert cli.main(["licence", "remove"]) == 0
    assert '"state": "none"' in capsys.readouterr().out


def test_warnings_before_expiry_and_in_grace(
    yugiverse: Ed25519PrivateKey, settings: Settings
) -> None:
    settings.licence_key = SecretStr(sign(yugiverse))  # expires NOW + 365 days
    calm = licence.status(None, settings, now=NOW)
    assert calm["state"] == "valid" and calm["warning"] is None and calm["days_left"] == 365
    soon = licence.status(None, settings, now=NOW + timedelta(days=345))
    assert soon["warning"].startswith("Your CostLens licence ends in 20 days")
    grace = licence.status(None, settings, now=NOW + timedelta(days=370))
    assert grace["state"] == "grace" and "keeps working until" in grace["warning"]
    assert grace["grace_days_left"] == 9
    gone = licence.status(None, settings, now=NOW + timedelta(days=400))
    assert gone["state"] == "expired" and "free tier" in gone["warning"]
    settings.licence_key = SecretStr("CLv1.bad.key")
    bad = licence.status(None, settings)
    assert bad["state"] == "invalid" and bad["warning"].startswith("The licence key cannot be used")
