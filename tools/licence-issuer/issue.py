#!/usr/bin/env python3
"""CloudCostRadar licence issuer (B.7). Runs on Yugiverse's own machine only: it is not part of
the product, the repository holds no key, and the image never contains this tool.

    # once: make the key pair (the private key file is created readable by you only)
    python issue.py keygen --out ~/ccradar-licence.key
    # -> prints the PUBLIC key to build into backend/src/ccradar/billing/licence.py

    # each sale or renewal
    python issue.py issue --key-file ~/ccradar-licence.key \\
        --customer "Acme Ltd" --plan business --days 365 --grace-days 14 \\
        --limit members=250 --feature partner --out acme.licence

    # check a key against a public key
    python issue.py verify --public-key=<base64url> acme.licence   # '=' needed: keys may start with '-'

The private key can also come from the ``CCRADAR_LICENCE_PRIVATE_KEY`` environment
variable (PEM text), e.g. injected by a secrets manager for one command. Cloud KMS
signing is not built: none of the major KMS services is set up for Yugiverse yet (Q7).

Needs only the ``cryptography`` package (Apache-2.0/BSD)."""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

PREFIX = "CCRv1"
ENV_KEY = "CCRADAR_LICENCE_PRIVATE_KEY"


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def public_text(key: Ed25519PrivateKey) -> str:
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return b64(raw)


def keygen(out: Path, passphrase: str | None) -> str:
    if out.exists():
        raise SystemExit(f"{out} exists: refusing to overwrite a private key")
    key = Ed25519PrivateKey.generate()
    algorithm = (
        serialization.BestAvailableEncryption(passphrase.encode())
        if passphrase
        else serialization.NoEncryption()
    )
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, algorithm
    )
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    return public_text(key)


def load_private(key_file: str | None, passphrase: str | None) -> Ed25519PrivateKey:
    if key_file:
        pem = Path(key_file).expanduser().read_bytes()
    elif os.environ.get(ENV_KEY):
        pem = os.environ[ENV_KEY].encode()
    else:
        raise SystemExit(f"give --key-file or set {ENV_KEY}")
    key = serialization.load_pem_private_key(
        pem, password=passphrase.encode() if passphrase else None
    )
    if not isinstance(key, Ed25519PrivateKey):
        raise SystemExit("this is not an Ed25519 private key")
    return key


def _limits(items: list[str]) -> dict[str, int | None]:
    out: dict[str, int | None] = {}
    for item in items:
        name, _, value = item.partition("=")
        if not name or not value:
            raise SystemExit(f"--limit takes name=number or name=unlimited, not {item!r}")
        out[name] = None if value == "unlimited" else int(value)
    return out


def issue(
    key: Ed25519PrivateKey,
    *,
    customer: str,
    plan: str,
    days: int,
    grace_days: int = 14,
    limits: dict[str, int | None] | None = None,
    features: list[str] | None = None,
    now: datetime | None = None,
) -> str:
    now = now or datetime.now(UTC)
    payload = {
        "v": 1,
        "id": str(uuid.uuid4()),
        "customer": customer,
        "plan": plan,
        "limits": limits or {},
        "features": features or [],
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(days=days)).isoformat(),
        "grace_days": grace_days,
    }
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return f"{PREFIX}.{b64(body)}.{b64(key.sign(body))}"


def verify(text: str, public_key: str) -> dict:
    prefix, body, sig = text.strip().split(".")
    if prefix != PREFIX:
        raise SystemExit("not a CloudCostRadar licence")
    Ed25519PublicKey.from_public_bytes(unb64(public_key)).verify(unb64(sig), unb64(body))
    return json.loads(unb64(body))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="issue.py", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    kg = sub.add_parser("keygen", help="make a new key pair")
    kg.add_argument("--out", required=True, type=Path)
    kg.add_argument("--passphrase-env", help="encrypt the private key with this variable's value")
    iss = sub.add_parser("issue", help="sign a licence")
    iss.add_argument("--key-file")
    iss.add_argument("--passphrase-env")
    iss.add_argument("--customer", required=True)
    iss.add_argument("--plan", required=True)
    iss.add_argument("--days", type=int, required=True)
    iss.add_argument("--grace-days", type=int, default=14)
    iss.add_argument("--limit", action="append", default=[], metavar="NAME=N")
    iss.add_argument("--feature", action="append", default=[])
    iss.add_argument("--out", type=Path, help="write here instead of printing")
    ver = sub.add_parser("verify", help="check a licence with a public key")
    ver.add_argument(
        "--public-key", required=True, help="use --public-key=KEY (keys may start with -)"
    )
    ver.add_argument("file", type=Path)
    args = parser.parse_args(argv)

    def passphrase() -> str | None:
        return os.environ.get(args.passphrase_env) if args.passphrase_env else None

    if args.command == "keygen":
        print(keygen(args.out.expanduser(), passphrase()))
        print(
            f"private key written to {args.out} (keep it offline, with a backup)",
            file=sys.stderr,
        )
        return 0
    if args.command == "issue":
        if args.days <= 0:
            raise SystemExit("--days must be positive")
        text = issue(
            load_private(args.key_file, passphrase()),
            customer=args.customer,
            plan=args.plan,
            days=args.days,
            grace_days=args.grace_days,
            limits=_limits(args.limit),
            features=args.feature,
        )
        if args.out:
            args.out.write_text(text + "\n", encoding="utf-8")
        else:
            print(text)
        return 0
    print(json.dumps(verify(args.file.read_text(encoding="utf-8"), args.public_key), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
