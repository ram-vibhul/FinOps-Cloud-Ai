"""Tamper-proof, expiring tokens for small payloads (e.g. the sign-in state cookie).

Format: ``base64url(json) . base64url(hmac_sha256)``. Keys are derived per
purpose, so a token minted for one use cannot be replayed for another.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any


class BadSignatureError(ValueError):
    pass


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


class Signer:
    def __init__(self, secret: str | bytes, purpose: str) -> None:
        secret_bytes = secret.encode() if isinstance(secret, str) else secret
        self._key = hmac.new(secret_bytes, b"costlens:" + purpose.encode(), hashlib.sha256).digest()

    def _mac(self, body: str) -> str:
        return _b64(hmac.new(self._key, body.encode(), hashlib.sha256).digest())

    def dumps(self, payload: dict[str, Any], *, max_age: int, now: float | None = None) -> str:
        data = {"d": payload, "x": int((now or time.time()) + max_age)}
        body = _b64(json.dumps(data, separators=(",", ":")).encode())
        return f"{body}.{self._mac(body)}"

    def loads(self, token: str, *, now: float | None = None) -> dict[str, Any]:
        try:
            body, mac = token.split(".", 1)
        except ValueError:
            raise BadSignatureError("malformed token") from None
        if not hmac.compare_digest(mac, self._mac(body)):
            raise BadSignatureError("bad signature")
        data = json.loads(_unb64(body))
        if data["x"] < (now or time.time()):
            raise BadSignatureError("expired")
        return data["d"]
