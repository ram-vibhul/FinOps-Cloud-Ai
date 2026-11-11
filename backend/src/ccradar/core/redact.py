"""Remove secrets and personal data from free text (event messages, logs) before it is
stored or sent anywhere else (AI wording, notifications). Patterns are deliberately
broad: a false positive costs a word, a false negative can leak a credential.
Every repetition is bounded so a long line can never cause catastrophic backtracking."""

from __future__ import annotations

import re
from dataclasses import dataclass

MASK = "[REDACTED]"


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern[str]
    replacement: str = MASK


def _r(name: str, pattern: str, replacement: str = MASK, flags: int = 0) -> Rule:
    return Rule(name, re.compile(pattern, flags), replacement)


SECRET_RULES: tuple[Rule, ...] = (
    _r(
        "private_key",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(-----END [A-Z ]*PRIVATE KEY-----|$)",
        "[REDACTED PRIVATE KEY]",
        re.S,
    ),
    # scheme://user:password@host -> keep scheme and host
    _r(
        "url_credentials",
        r"(?P<scheme>\b[a-z][a-z0-9+.-]{0,20}://)[^\s:/@]{1,256}:[^\s@/]{1,256}@",
        r"\g<scheme>[REDACTED]@",
        re.I,
    ),
    _r("jwt", r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    _r("aws_access_key", r"\b(AKIA|ASIA|AGPA|AIDA|AROA|ANPA)[A-Z0-9]{16}\b"),
    _r("github_token", r"\b(gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b"),
    _r("slack_token", r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b"),
    _r("stripe_key", r"\b(sk|rk|pk)_(live|test)_[A-Za-z0-9]{16,}\b"),
    _r("google_api_key", r"\bAIza[0-9A-Za-z_-]{35}\b"),
    _r("anthropic_key", r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"),
    _r("openai_key", r"\bsk-(proj-)?[A-Za-z0-9_-]{32,}\b"),
    _r("ccradar_token", r"\bckt_[0-9a-f]{32}_[A-Za-z0-9_-]{20,}\b"),
    _r("bearer", r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{12,}", r"\1 [REDACTED]"),
    # key=value / key: value / "key": "value" for secret-looking keys
    _r(
        "secret_assignment",
        r"""(?ix)
       (?P<key>(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|
          private[_-]?key|client[_-]?secret|auth|credential|session[_-]?id|cookie)
          [\w.-]{0,40}+["']?+)
       (?P<sep>\s{0,5}+[:=]\s{0,5}+)
       (?:"[^"\n]{3,1000}+"|'[^'\n]{3,1000}+'|[^\s"',;&}]{3,1000}+)""",
        r"\g<key>\g<sep>[REDACTED]",
    ),
    # long random-looking strings (hex/base64) that are probably keys
    _r("long_hex", r"\b[0-9a-fA-F]{40,}\b"),
)

EMAIL = _r("email", r"\b[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,24}\b", "[EMAIL]")
PHONE = _r("phone", r"(?<![\w.])\+?\d[\d ()-]{8,18}\d(?![\w.])", "[PHONE]")
IPV4 = _r("ipv4", r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b", "[IP]")


def redact(text: str | None, *, personal: bool = True, ips: bool = False) -> str:
    """Secrets always; emails and phone numbers unless ``personal`` is off; IPs only when
    asked (they are often needed to debug networking)."""
    if not text:
        return text or ""
    out = text
    for rule in SECRET_RULES:
        out = rule.pattern.sub(rule.replacement, out)
    if personal:
        out = EMAIL.pattern.sub(EMAIL.replacement, out)
        out = PHONE.pattern.sub(PHONE.replacement, out)
    if ips:
        out = IPV4.pattern.sub(IPV4.replacement, out)
    return out
