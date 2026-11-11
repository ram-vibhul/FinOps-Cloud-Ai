"""The issuing tool (tools/licence-issuer) and the product agree on the format (B.7)."""

from __future__ import annotations

import importlib.util
import os
import stat
from pathlib import Path

import pytest

from ccradar.billing import licence

TOOL = Path(__file__).resolve().parents[2] / "tools" / "licence-issuer" / "issue.py"


@pytest.fixture
def tool():
    spec = importlib.util.spec_from_file_location("licence_issuer", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_keygen_issue_and_product_verify(tool, tmp_path: Path, capsys, monkeypatch) -> None:
    key_file = tmp_path / "yugiverse.key"
    assert tool.main(["keygen", "--out", str(key_file)]) == 0
    public = capsys.readouterr().out.strip()
    assert stat.S_IMODE(os.stat(key_file).st_mode) == 0o600  # readable by the owner only
    with pytest.raises(SystemExit, match="refusing to overwrite"):
        tool.main(["keygen", "--out", str(key_file)])
    out = tmp_path / "acme.licence"
    args = ["issue", "--key-file", str(key_file), "--customer", "Acme Ltd", "--plan", "team"]
    args += ["--days", "30", "--limit", "members=40", "--limit", "clusters=unlimited"]
    assert tool.main([*args, "--feature", "sso", "--out", str(out)]) == 0
    monkeypatch.setattr(licence, "PUBLIC_KEYS", (public,))
    lic = licence.decode(out.read_text())
    assert (lic.customer, lic.plan, lic.limits, lic.features) == (
        "Acme Ltd",
        "team",
        {"members": 40, "clusters": None},
        ("sso",),
    )
    assert lic.state() == "valid"
    assert tool.main(["verify", f"--public-key={public}", str(out)]) == 0  # keys may start with "-"


def test_key_from_environment_with_passphrase(tool, tmp_path: Path, capsys, monkeypatch) -> None:
    key_file = tmp_path / "k.key"
    monkeypatch.setenv("PASS", "correct horse")
    tool.main(["keygen", "--out", str(key_file), "--passphrase-env", "PASS"])
    public = capsys.readouterr().out.strip()
    monkeypatch.setenv(tool.ENV_KEY, key_file.read_text())
    tool.main(
        ["issue", "--passphrase-env", "PASS", "--customer", "X", "--plan", "free", "--days", "1"]
    )
    text = capsys.readouterr().out.strip()
    monkeypatch.setattr(licence, "PUBLIC_KEYS", (public,))
    assert licence.decode(text).customer == "X"
    monkeypatch.delenv("PASS")
    with pytest.raises(TypeError):  # encrypted key without its passphrase
        tool.main(["issue", "--customer", "X", "--plan", "free", "--days", "1"])


def test_tool_is_not_in_the_image() -> None:
    ignore = (TOOL.parents[2] / ".dockerignore").read_text()
    assert "tools/licence-issuer" in ignore and "*.key" in ignore
