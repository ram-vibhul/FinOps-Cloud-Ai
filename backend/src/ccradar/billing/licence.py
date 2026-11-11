"""Self-hosted licences (B.6): offline, Ed25519-signed keys.

A licence key is ``CCRv1.<payload>.<signature>`` (base64url, no padding). The payload is
JSON::

    {"v": 1, "id": "...", "customer": "...", "plan": "business",
     "limits": {"members": 250}, "features": ["partner"],
     "issued_at": "2026-11-09T00:00:00+00:00", "expires_at": "2027-11-09T00:00:00+00:00",
     "grace_days": 14}

The signature is Ed25519 over the payload bytes. Only Yugiverse's **public** keys are
built into CloudCostRadar (:data:`PUBLIC_KEYS`); they are deliberately not a setting, or anyone
could trust a key of their own and sign licences. The private key stays on the issuer's
machine (B.7) and is never in the repository or the image.

States: ``valid`` → ``grace`` (expired, still working for ``grace_days`` with warnings)
→ ``expired`` (back to the free tier; nothing is deleted). Without a licence, a
self-hosted installation runs the free tier."""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy.orm import Session

from ccradar.config import Settings
from ccradar.db.base import utcnow
from ccradar.models import InstanceSetting

log = logging.getLogger(__name__)
PREFIX = "CCRv1"
SETTING_KEY = "licence"
# Yugiverse's licence-signing public keys (raw 32-byte keys, base64url). Empty until the
# production key pair is made with the issuing tool (tracker Q14); newest first, so a
# key can be rotated while licences signed with the older one keep working.
PUBLIC_KEYS: tuple[str, ...] = ()
WARN_DAYS = 30  # warn this long before a licence ends (B.14)


class LicenceError(ValueError):
    pass


@dataclass(frozen=True)
class Licence:
    id: str
    customer: str
    plan: str
    issued_at: datetime
    expires_at: datetime
    grace_days: int = 14
    limits: dict[str, int | None] = field(default_factory=dict)
    features: tuple[str, ...] = ()

    def state(self, now: datetime | None = None) -> str:
        now = now or utcnow()
        if now < self.expires_at:
            return "valid"
        if now < self.expires_at + timedelta(days=self.grace_days):
            return "grace"
        return "expired"


def _b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _when(value: Any) -> datetime:
    when = datetime.fromisoformat(str(value))
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def decode(text: str, keys: tuple[str, ...] | None = None) -> Licence:
    """Check the signature with Yugiverse's public keys and read the licence."""
    parts = text.strip().split(".")
    if len(parts) != 3 or parts[0] != PREFIX:
        raise LicenceError("This is not a CloudCostRadar licence key.")
    try:
        payload, signature = _b64decode(parts[1]), _b64decode(parts[2])
    except ValueError as exc:
        raise LicenceError("The licence key is damaged.") from exc
    for key in PUBLIC_KEYS if keys is None else keys:
        try:
            Ed25519PublicKey.from_public_bytes(_b64decode(key)).verify(signature, payload)
            break
        except (InvalidSignature, ValueError):
            continue
    else:
        raise LicenceError("The licence key was not issued by Yugiverse.")
    try:
        data = json.loads(payload)
        if data.get("v") != 1:
            raise LicenceError("This licence needs a newer version of CloudCostRadar.")
        return Licence(
            id=str(data["id"]),
            customer=str(data["customer"]),
            plan=str(data["plan"]),
            issued_at=_when(data["issued_at"]),
            expires_at=_when(data["expires_at"]),
            grace_days=int(data.get("grace_days", 14)),
            limits=dict(data.get("limits") or {}),
            features=tuple(data.get("features") or ()),
        )
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, LicenceError):
            raise
        raise LicenceError("The licence key is damaged.") from exc


def installed_text(db: Session | None, settings: Settings) -> str | None:
    """The licence key: ``CCRADAR_LICENCE_KEY``, else ``CCRADAR_LICENCE_FILE``, else the
    one installed through the API or CLI."""
    if settings.licence_key is not None:
        return settings.licence_key.get_secret_value()
    if settings.licence_file:
        try:
            return Path(settings.licence_file).read_text(encoding="utf-8").strip()
        except OSError:
            log.warning("licence file %s cannot be read", settings.licence_file)
            return None
    if db is None:
        return None
    row = db.get(InstanceSetting, SETTING_KEY)
    return (row.value or {}).get("key") if row else None


def current(db: Session | None, settings: Settings) -> tuple[Licence | None, str | None]:
    """(licence, problem): the installed licence if it verifies, else why not."""
    text = installed_text(db, settings)
    if not text:
        return None, None
    try:
        return decode(text), None
    except LicenceError as exc:
        return None, str(exc)


def install(db: Session, text: str) -> Licence:
    lic = decode(text)  # refuse anything that does not verify
    row = db.get(InstanceSetting, SETTING_KEY)
    if row is None:
        db.add(InstanceSetting(key=SETTING_KEY, value={"key": text.strip()}))
    else:
        row.value = {"key": text.strip()}
    db.flush()
    return lic


def remove(db: Session) -> None:
    row = db.get(InstanceSetting, SETTING_KEY)
    if row is not None:
        db.delete(row)
        db.flush()


def _warning(state: str, days_left: int, grace_end: datetime) -> str | None:
    when = grace_end.strftime("%d %B %Y")
    if state == "valid" and days_left <= WARN_DAYS:
        return (
            f"Your CloudCostRadar licence ends in {days_left} day{'s' if days_left != 1 else ''}. "
            "Renew it to keep your plan."
        )
    if state == "grace":
        return (
            f"Your CloudCostRadar licence has expired. Everything keeps working until {when}; "
            "after that this installation moves to the free tier. Nothing is deleted."
        )
    if state == "expired":
        return "Your CloudCostRadar licence has expired; this installation runs the free tier."
    return None


def status(db: Session | None, settings: Settings, now: datetime | None = None) -> dict[str, Any]:
    """What the licence page, the banner and the CLI show (B.14)."""
    lic, problem = current(db, settings)
    if lic is None:
        warning = f"The licence key cannot be used: {problem}" if problem else None
        return {"state": "invalid" if problem else "none", "problem": problem, "warning": warning}
    now = now or utcnow()
    state = lic.state(now)
    grace_end = lic.expires_at + timedelta(days=lic.grace_days)
    days_left = max(0, (lic.expires_at - now).days)
    return {
        "warning": _warning(state, days_left, grace_end),
        "state": state,
        "problem": None,
        "id": lic.id,
        "customer": lic.customer,
        "plan": lic.plan,
        "expires_at": lic.expires_at.isoformat(),
        "days_left": days_left,
        "grace_ends_at": grace_end.isoformat(),
        "grace_days_left": max(0, (grace_end - now).days) if state == "grace" else None,
        "limits": lic.limits,
        "features": list(lic.features),
    }
