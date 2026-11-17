"""Audit-log streaming to a SIEM (11.42).

Each stream keeps a cursor over the audit log, ordered by (occurred_at, id). Every minute
a job sends what is new, in batches, and moves the cursor only after the receiver
accepted the batch. A failed batch is sent again later from the same place (after 1 min,
5 min, 30 min, 2 h, then every 6 h), so no event is skipped or dropped. Events newer than
``SETTLE`` are left for the next run, so a transaction that commits late is not missed.

Destinations:

- ``splunk_hec``: Splunk HTTP Event Collector, ``POST <url>/services/collector/event``
  with ``Authorization: Splunk <token>``; one JSON event object per line.
- ``sentinel``: Microsoft Sentinel through the Azure Monitor Logs Ingestion API (a data
  collection endpoint and rule). An Entra app's client secret gets a token for
  ``https://monitor.azure.com``; the batch is a JSON array.
- ``datadog``: Datadog Logs intake ``/api/v2/logs`` for the chosen site, ``DD-API-KEY``.
- ``syslog``: RFC 5424 messages over TLS (RFC 5425, octet-counted), port 6514 by default.
- ``webhook``: a JSON ``{"events": [...]}`` POST to any public HTTPS address.

Signing: every HTTP request carries ``X-CloudCostRadar-Signature: t=<unix>,v1=<hex>``,
HMAC-SHA256 with the stream's signing secret over ``"<t>.<raw body>"`` (the same scheme
as outgoing webhooks). Syslog messages carry the HMAC of their JSON body in the
structured data ``[ccradar@32473 sig="<hex>"]``. Only public internet addresses are
allowed, checked when saving and before every send.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import socket
import ssl
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from ccradar.core.netguard import (
    Resolver,
    UnsafeURLError,
    check_public_https_url,
    public_addresses,
)
from ccradar.core.webhooks_out import new_secret, sign
from ccradar.db.base import utcnow
from ccradar.models import AuditEvent, AuditStream, Organization

log = logging.getLogger(__name__)
KINDS = {
    "splunk_hec": "Splunk (HTTP Event Collector)",
    "sentinel": "Microsoft Sentinel",
    "datadog": "Datadog Logs",
    "syslog": "Syslog over TLS",
    "webhook": "Webhook (signed JSON)",
}
DATADOG_SITES = {
    "datadoghq.com": "http-intake.logs.datadoghq.com",
    "us3.datadoghq.com": "http-intake.logs.us3.datadoghq.com",
    "us5.datadoghq.com": "http-intake.logs.us5.datadoghq.com",
    "datadoghq.eu": "http-intake.logs.datadoghq.eu",
    "ap1.datadoghq.com": "http-intake.logs.ap1.datadoghq.com",
    "ap2.datadoghq.com": "http-intake.logs.ap2.datadoghq.com",
    "ddog-gov.com": "http-intake.logs.ddog-gov.com",
}
BACKOFF = [
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=30),
    timedelta(hours=2),
    timedelta(hours=6),
]
SETTLE = timedelta(minutes=2)
BATCH = 500
MAX_BATCHES = 10  # per stream per run; the rest goes next minute
TIMEOUT = 15.0
SD_ID = "ccradar@32473"  # 32473: the example enterprise number (RFC 5612)

SyslogSender = Callable[[str, int, list[str], list[bytes]], None]


class StreamError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Transport:
    http: httpx.Client
    resolver: Resolver
    syslog: SyslogSender | None = None  # set in tests; real TLS sockets otherwise


def new_signing_secret() -> str:
    return new_secret()


# --- settings ---------------------------------------------------------------------------


def _https(url: str, resolver: Resolver, ports: tuple[int | None, ...] = (None, 443)) -> str:
    url = url.strip().rstrip("/")
    try:
        return check_public_https_url(url, resolver, ports)
    except UnsafeURLError as exc:
        raise StreamError(f"This address cannot be used: {exc}.") from exc


def _need(s: dict[str, Any], *keys: str) -> None:
    missing = [k for k in keys if not s.get(k)]
    if missing:
        raise StreamError(f"Missing: {', '.join(missing)}.")


def _splunk_settings(s: dict[str, Any], resolver: Resolver) -> dict[str, Any]:
    _need(s, "url")
    out = {"url": _https(s["url"], resolver, (None, 443, 8088))}
    for key in ("index", "sourcetype"):
        if s.get(key):
            out[key] = str(s[key])[:100]
    return out


def _sentinel_settings(s: dict[str, Any], resolver: Resolver) -> dict[str, Any]:
    _need(s, "tenant_id", "client_id", "endpoint", "dcr_id", "stream")
    try:
        uuid.UUID(str(s["tenant_id"]))
        uuid.UUID(str(s["client_id"]))
    except ValueError as exc:
        raise StreamError("The tenant and client IDs are GUIDs.") from exc
    endpoint = _https(s["endpoint"], resolver)
    if not (urlsplit(endpoint).hostname or "").endswith(".ingest.monitor.azure.com"):
        raise StreamError("The endpoint is a data collection endpoint (…ingest.monitor.azure.com).")
    if not str(s["dcr_id"]).startswith("dcr-"):
        raise StreamError("The rule ID is the immutable ID, starting with dcr-.")
    if not str(s["stream"]).startswith("Custom-"):
        raise StreamError("The stream name starts with Custom-.")
    return {
        "tenant_id": str(s["tenant_id"]),
        "client_id": str(s["client_id"]),
        "endpoint": endpoint,
        "dcr_id": str(s["dcr_id"])[:100],
        "stream": str(s["stream"])[:100],
    }


def _datadog_settings(s: dict[str, Any], resolver: Resolver) -> dict[str, Any]:
    site = s.get("site") or "datadoghq.com"
    if site not in DATADOG_SITES:
        raise StreamError(f"Unknown Datadog site {site}.")
    out = {"site": site}
    if s.get("tags"):
        out["tags"] = str(s["tags"])[:300]
    return out


def _syslog_settings(s: dict[str, Any], resolver: Resolver) -> dict[str, Any]:
    _need(s, "host")
    host = str(s["host"]).lower()
    if "/" in host or ":" in host:
        raise StreamError("Give the host name only, and the port separately.")
    try:
        port = int(s.get("port") or 6514)
    except (TypeError, ValueError) as exc:
        raise StreamError("The port is a number.") from exc
    if not 1 <= port <= 65535:
        raise StreamError("The port is between 1 and 65535.")
    try:
        public_addresses(host, resolver)
    except UnsafeURLError as exc:
        raise StreamError(f"This address cannot be used: {exc}.") from exc
    return {"host": host, "port": port}


def _webhook_settings(s: dict[str, Any], resolver: Resolver) -> dict[str, Any]:
    _need(s, "url")
    return {"url": _https(s["url"], resolver)}


_SETTINGS = {
    "splunk_hec": _splunk_settings,
    "sentinel": _sentinel_settings,
    "datadog": _datadog_settings,
    "syslog": _syslog_settings,
    "webhook": _webhook_settings,
}


def clean_settings(kind: str, raw: dict[str, Any], resolver: Resolver) -> dict[str, Any]:
    """Validate a destination's settings (raises StreamError with a plain message)."""
    if kind not in _SETTINGS:
        raise StreamError(f"Unknown destination {kind}.")
    s = {k: (v.strip() if isinstance(v, str) else v) for k, v in raw.items() if v not in (None, "")}
    return _SETTINGS[kind](s, resolver)


def needs_secret(kind: str) -> bool:
    return kind in ("splunk_hec", "sentinel", "datadog")


# --- events -----------------------------------------------------------------------------


def as_dict(e: AuditEvent, org_slug: str | None) -> dict[str, Any]:
    at = e.occurred_at if e.occurred_at.tzinfo else e.occurred_at.replace(tzinfo=UTC)
    return {
        "id": str(e.id),
        "occurred_at": at.isoformat(),
        "org_id": str(e.org_id),
        "org": org_slug,
        "actor": {
            "type": e.actor_type,
            "user_id": str(e.actor_user_id) if e.actor_user_id else None,
            "label": e.actor_label,
        },
        "action": e.action,
        "target": {"type": e.target_type, "id": e.target_id},
        "summary": e.summary,
        "changes": e.changes or {},
        "ip": e.ip,
        "user_agent": e.user_agent,
        "request_id": e.request_id,
    }


def pending(
    db: Session, stream: AuditStream, now: datetime, limit: int = BATCH
) -> list[AuditEvent]:
    t = AuditEvent
    q = select(t).where(t.org_id == stream.org_id, t.occurred_at <= now - SETTLE)
    if stream.cursor_at is not None:
        cursor_id = stream.cursor_id or uuid.UUID(int=0)
        q = q.where(
            or_(
                t.occurred_at > stream.cursor_at,
                and_(t.occurred_at == stream.cursor_at, t.id > cursor_id),
            )
        )
    return list(db.scalars(q.order_by(t.occurred_at, t.id).limit(limit)))


# --- senders ----------------------------------------------------------------------------


def _signed_headers(stream: AuditStream, body: bytes, now: datetime) -> dict[str, str]:
    return {
        "User-Agent": "CloudCostRadar-AuditStream/1",
        "X-CloudCostRadar-Signature": sign(stream.signing_secret, body, int(now.timestamp())),
    }


def _post(
    tr: Transport,
    url: str,
    body: bytes,
    headers: dict[str, str],
    ports: tuple[int | None, ...] = (None, 443),
) -> int:
    try:
        check_public_https_url(url, tr.resolver, ports)
        r = tr.http.post(
            url, content=body, headers=headers, timeout=TIMEOUT, follow_redirects=False
        )
    except UnsafeURLError as exc:
        raise StreamError(f"address not allowed: {exc}") from exc
    except httpx.HTTPError as exc:
        raise StreamError(type(exc).__name__) from exc
    if not 200 <= r.status_code < 300:
        raise StreamError(f"answered {r.status_code}", r.status_code)
    return r.status_code


def _splunk(stream: AuditStream, events: list[dict[str, Any]], tr: Transport, now: datetime) -> int:
    cfg = stream.settings
    lines = []
    for e in events:
        item: dict[str, Any] = {
            "time": datetime.fromisoformat(e["occurred_at"]).timestamp(),
            "host": "cloudcostradar",
            "source": "cloudcostradar",
            "sourcetype": cfg.get("sourcetype") or "cloudcostradar:audit",
            "event": e,
        }
        if cfg.get("index"):
            item["index"] = cfg["index"]
        lines.append(json.dumps(item, separators=(",", ":"), sort_keys=True))
    body = "\n".join(lines).encode()
    headers = {
        **_signed_headers(stream, body, now),
        "Content-Type": "application/json",
        "Authorization": f"Splunk {stream.secret}",
    }
    return _post(tr, f"{cfg['url']}/services/collector/event", body, headers, (None, 443, 8088))


def _sentinel_token(stream: AuditStream, tr: Transport) -> str:
    cfg = stream.settings
    url = f"https://login.microsoftonline.com/{cfg['tenant_id']}/oauth2/v2.0/token"
    try:
        r = tr.http.post(
            url,
            data={
                "grant_type": "client_credentials",
                "client_id": cfg["client_id"],
                "client_secret": stream.secret or "",
                "scope": "https://monitor.azure.com//.default",
            },
            timeout=TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise StreamError(f"sign-in failed: {type(exc).__name__}") from exc
    if r.status_code != 200:
        raise StreamError(f"Microsoft Entra sign-in answered {r.status_code}", r.status_code)
    token = (r.json() or {}).get("access_token")
    if not token:
        raise StreamError("Microsoft Entra sign-in gave no token")
    return str(token)


def _sentinel(
    stream: AuditStream, events: list[dict[str, Any]], tr: Transport, now: datetime
) -> int:
    cfg = stream.settings
    rows = [
        {
            "TimeGenerated": e["occurred_at"],
            "EventId": e["id"],
            "Action": e["action"],
            "ActorType": e["actor"]["type"],
            "Actor": e["actor"]["label"],
            "TargetType": e["target"]["type"],
            "TargetId": e["target"]["id"],
            "Summary": e["summary"],
            "SourceIp": e["ip"],
            "Organization": e["org"],
            "Details": json.dumps(e, separators=(",", ":"), sort_keys=True),
        }
        for e in events
    ]
    body = json.dumps(rows, separators=(",", ":")).encode()
    token = _sentinel_token(stream, tr)
    url = (
        f"{cfg['endpoint']}/dataCollectionRules/{cfg['dcr_id']}/streams/{cfg['stream']}"
        "?api-version=2023-01-01"
    )
    headers = {
        **_signed_headers(stream, body, now),
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    return _post(tr, url, body, headers)


def _datadog(
    stream: AuditStream, events: list[dict[str, Any]], tr: Transport, now: datetime
) -> int:
    cfg = stream.settings
    extra = cfg.get("tags")
    items = []
    for e in events:
        tags = [f"action:{e['action']}", f"actor_type:{e['actor']['type']}"]
        if e.get("org"):
            tags.append(f"org:{e['org']}")
        if extra:
            tags.append(extra)
        items.append(
            {
                "ddsource": "cloudcostradar",
                "service": "cloudcostradar",
                "hostname": "cloudcostradar",
                "ddtags": ",".join(tags),
                "message": e["summary"] or e["action"],
                "date": e["occurred_at"],
                "audit": e,
            }
        )
    body = json.dumps(items, separators=(",", ":")).encode()
    headers = {
        **_signed_headers(stream, body, now),
        "Content-Type": "application/json",
        "DD-API-KEY": stream.secret or "",
    }
    return _post(tr, f"https://{DATADOG_SITES[cfg['site']]}/api/v2/logs", body, headers)


def _webhook(
    stream: AuditStream, events: list[dict[str, Any]], tr: Transport, now: datetime
) -> int:
    body = json.dumps({"events": events}, separators=(",", ":"), sort_keys=True).encode()
    headers = {
        **_signed_headers(stream, body, now),
        "Content-Type": "application/json",
        "X-CloudCostRadar-Event": "audit.batch",
    }
    return _post(tr, stream.settings["url"], body, headers)


def syslog_frame(stream: AuditStream, e: dict[str, Any]) -> bytes:
    """One RFC 5424 message, octet-counted (RFC 6587/5425). Facility 13 (log audit),
    severity 6 (informational): PRI 110."""
    msg = json.dumps(e, separators=(",", ":"), sort_keys=True)
    mac = hmac.new(stream.signing_secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
    action = "".join(c for c in e["action"] if 33 <= ord(c) <= 126)[:32] or "-"
    line = (
        f"<110>1 {e['occurred_at']} cloudcostradar cloudcostradar - {action} "
        f'[{SD_ID} id="{e["id"]}" sig="{mac}"] ﻿{msg}'
    ).encode()
    return f"{len(line)} ".encode() + line


def tls_syslog(host: str, port: int, addresses: list[str], frames: list[bytes]) -> None:
    """Connect to a checked address (not a fresh DNS answer) and verify the certificate
    for the host name."""
    context = ssl.create_default_context()
    last: Exception | None = None
    for address in addresses:
        try:
            with (
                socket.create_connection((address, port), timeout=TIMEOUT) as raw,
                context.wrap_socket(raw, server_hostname=host) as tls,
            ):
                for frame in frames:
                    tls.sendall(frame)
                return
        except OSError as exc:
            last = exc
    raise StreamError(f"syslog: {type(last).__name__ if last else 'no address'}")


def _syslog(stream: AuditStream, events: list[dict[str, Any]], tr: Transport, now: datetime) -> int:
    cfg = stream.settings
    try:
        addresses = public_addresses(cfg["host"], tr.resolver)
    except UnsafeURLError as exc:
        raise StreamError(f"address not allowed: {exc}") from exc
    frames = [syslog_frame(stream, e) for e in events]
    (tr.syslog or tls_syslog)(cfg["host"], int(cfg.get("port") or 6514), addresses, frames)
    return 0


SENDERS = {
    "splunk_hec": _splunk,
    "sentinel": _sentinel,
    "datadog": _datadog,
    "syslog": _syslog,
    "webhook": _webhook,
}


# --- running ----------------------------------------------------------------------------


def _fail(stream: AuditStream, exc: StreamError, now: datetime) -> None:
    stream.failures += 1
    stream.last_status, stream.last_error = exc.status, str(exc)[:500]
    stream.next_attempt_at = now + BACKOFF[min(stream.failures, len(BACKOFF)) - 1]
    if stream.failures in (3, 10):
        log.warning("audit stream %s failing: %s", stream.id, exc)


def send_batch(
    stream: AuditStream, events: list[dict[str, Any]], tr: Transport, now: datetime
) -> int | None:
    return SENDERS[stream.kind](stream, events, tr, now)


def run_stream(db: Session, stream: AuditStream, tr: Transport, now: datetime | None = None) -> int:
    """Send what is new for one stream; returns how many events were accepted."""
    now = now or utcnow()
    org = db.get(Organization, stream.org_id)
    slug = org.slug if org else None
    sent = 0
    for _ in range(MAX_BATCHES):
        rows = pending(db, stream, now)
        if not rows:
            break
        try:
            status = send_batch(stream, [as_dict(e, slug) for e in rows], tr, now)
        except StreamError as exc:
            _fail(stream, exc, now)
            break
        last = rows[-1]
        stream.cursor_at, stream.cursor_id = last.occurred_at, last.id
        stream.failures, stream.last_error, stream.next_attempt_at = 0, None, None
        stream.last_status, stream.last_sent_at = status or None, now
        stream.sent_total += len(rows)
        sent += len(rows)
        if len(rows) < BATCH:
            break
    db.flush()
    return sent


def send_test(stream: AuditStream, tr: Transport, org_slug: str | None) -> None:
    """Send one ``audit.test`` event now, outside the cursor (raises StreamError)."""
    now = utcnow()
    event = {
        "id": str(uuid.uuid4()),
        "occurred_at": now.isoformat(),
        "org_id": str(stream.org_id),
        "org": org_slug,
        "actor": {"type": "system", "user_id": None, "label": "CloudCostRadar"},
        "action": "audit.test",
        "target": {"type": "audit_stream", "id": str(stream.id)},
        "summary": "Test event: this audit-log stream works.",
        "changes": {},
        "ip": None,
        "user_agent": None,
        "request_id": None,
    }
    status = send_batch(stream, [event], tr, now)
    stream.last_status, stream.last_error, stream.last_sent_at = status or None, None, now


def due(db: Session, now: datetime) -> list[AuditStream]:
    return list(
        db.scalars(
            select(AuditStream).where(
                AuditStream.enabled.is_(True),
                or_(AuditStream.next_attempt_at.is_(None), AuditStream.next_attempt_at <= now),
            )
        )
    )
