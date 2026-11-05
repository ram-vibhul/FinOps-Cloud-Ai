"""Outbound URL safety for addresses supplied by customers (SSRF protection).

Customer-supplied URLs (an organisation's identity provider, webhooks in 6.4)
must be public HTTPS endpoints: the host must resolve only to public IP
addresses. Instance-level configuration written by the operator is trusted.

Known gap (closed in 6.4): the HTTP client resolves the name again when it
connects, so a DNS-rebinding attacker could still race this check.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlsplit

Resolver = Callable[[str], list[str]]


class UnsafeURLError(ValueError):
    pass


def system_resolver(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    return list({str(info[4][0]) for info in infos})


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def check_public_https_url(url: str, resolver: Resolver = system_resolver) -> str:
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise UnsafeURLError("only https:// addresses are allowed")
    if parts.username or parts.password:
        raise UnsafeURLError("credentials in the address are not allowed")
    host = parts.hostname
    if not host:
        raise UnsafeURLError("address has no host")
    if parts.port not in (None, 443):
        raise UnsafeURLError("only the standard HTTPS port is allowed")
    try:
        addresses = [host] if _looks_like_ip(host) else resolver(host)
    except OSError as exc:
        raise UnsafeURLError(f"cannot resolve {host}") from exc
    if not addresses or not all(_is_public(a) for a in addresses):
        raise UnsafeURLError(f"{host} is not a public internet address")
    return url


def _looks_like_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True
