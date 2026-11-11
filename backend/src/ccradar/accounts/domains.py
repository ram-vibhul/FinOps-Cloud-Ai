"""Verified email domains: the organisation adds a DNS TXT record to prove it owns a domain.

Record: ``_ccradar-verification.<domain>  TXT  "ccradar-verification=<token>"``.
Looked up with DNS-over-HTTPS (``CCRADAR_DNS_OVER_HTTPS_URL``), so no DNS library is
needed. A domain can be verified by one organisation only; public mail providers
(gmail.com and the like) cannot be verified at all.
"""

from __future__ import annotations

import re
import secrets

import httpx

from ccradar.db.base import utcnow
from ccradar.models import OrgDomain

DOMAIN = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
PUBLIC_MAIL = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "yahoo.com",
    "icloud.com", "me.com", "aol.com", "proton.me", "protonmail.com", "gmx.com", "zoho.com",
    "yandex.com", "mail.com", "rediffmail.com",
})  # fmt: skip
PREFIX = "_ccradar-verification"


def normalise(domain: str) -> str:
    d = domain.strip().lower().rstrip(".").removeprefix("@")
    if not DOMAIN.match(d):
        raise ValueError("not a domain name")
    if d in PUBLIC_MAIL:
        raise ValueError("public email domains cannot be verified")
    return d


def new_token() -> str:
    return secrets.token_hex(16)


def record(d: OrgDomain) -> dict[str, str]:
    return {
        "name": f"{PREFIX}.{d.domain}",
        "type": "TXT",
        "value": f"ccradar-verification={d.token}",
    }


def txt_records(http: httpx.Client, url: str, name: str) -> list[str]:
    r = http.get(
        url,
        params={"name": name, "type": "TXT"},
        headers={"accept": "application/dns-json"},
        timeout=10.0,
    )
    r.raise_for_status()
    return [
        str(a.get("data", "")).strip('"').replace('" "', "")
        for a in r.json().get("Answer") or []
        if a.get("type") == 16
    ]


def check(http: httpx.Client, url: str, d: OrgDomain) -> bool:
    d.last_checked_at = utcnow()
    try:
        found = txt_records(http, url, record(d)["name"])
    except (httpx.HTTPError, ValueError) as exc:
        d.last_error = f"DNS lookup failed ({type(exc).__name__})"
        return False
    if record(d)["value"] in found:
        d.verified_at, d.last_error = d.verified_at or utcnow(), None
        return True
    d.last_error = "The TXT record was not found yet (DNS changes can take a while)."
    return False
