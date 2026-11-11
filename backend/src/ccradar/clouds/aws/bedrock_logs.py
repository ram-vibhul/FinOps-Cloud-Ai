"""Bedrock spend per application and team from model invocation logs (read-only).

Bedrock can write one JSON record per model call to S3. CloudCostRadar reads those files
from the bucket the customer names (and only that bucket and prefix, through the
read-only role), and keeps **only** counts and attribution: time, model, input/output
and cache token counts, whether it failed, and app/team/feature. Prompt and response
text, when logging includes it, are dropped as each record is parsed and never stored
or logged. Turning off text delivery in the Bedrock logging settings is recommended.

Attribution, first match wins:
1. ``requestMetadata`` sent with the call (keys app/application, team, feature);
2. the customer's rules mapping the caller's IAM role to an app and team;
3. the caller's role name as the app.

Each run re-reads the last few days and replaces what it read before for those days,
so it can safely run again.
"""

from __future__ import annotations

import gzip
import io
import json
import re
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import delete
from sqlalchemy.orm import Session

from ccradar.clouds.aws.client import AwsAccessError, AwsGateway
from ccradar.costs import ai_usage
from ccradar.db.base import utcnow
from ccradar.db.tenant import set_tenant
from ccradar.models import AiUsageEvent, CloudConnection, Organization

DAY = re.compile(r"/(\d{4})/(\d{2})/(\d{2})/")
MAX_KEYS = 20_000
NAME = re.compile(r"[^\w.:/@+\-]+")


def _ident(value: Any, limit: int = 100) -> str | None:
    if value is None:
        return None
    s = NAME.sub("-", str(value).strip())[:limit].strip("-")
    return s or None


def _role(arn: str | None) -> str | None:
    if not arn:
        return None
    m = (
        re.search(r":assumed-role/([^/]+)/", arn)
        or re.search(r":role/(?:.*/)?([^/]+)$", arn)
        or re.search(r":user/(?:.*/)?([^/]+)$", arn)
    )
    return m.group(1) if m else None


def attribute(record: dict[str, Any], rules: list[dict[str, str]]) -> dict[str, str | None]:
    meta = {str(k).lower(): v for k, v in (record.get("requestMetadata") or {}).items()}
    arn = (record.get("identity") or {}).get("arn")
    out: dict[str, str | None] = {
        "app": _ident(meta.get("app") or meta.get("application")),
        "team": _ident(meta.get("team")),
        "feature": _ident(meta.get("feature")),
    }
    if not (out["app"] and out["team"]):
        for rule in rules:
            if arn and rule.get("match") and rule["match"] in arn:
                out["app"] = out["app"] or _ident(rule.get("app"))
                out["team"] = out["team"] or _ident(rule.get("team"))
                break
    out["app"] = out["app"] or _ident(_role(arn))
    return out


def counts(record: dict[str, Any]) -> dict[str, Any]:
    """The only fields CloudCostRadar keeps from a record. Text fields are never read."""
    inp, out = record.get("input") or {}, record.get("output") or {}
    return {
        "at": record.get("timestamp"),
        "model": str(record.get("modelId") or "unknown"),
        "input_tokens": int(inp.get("inputTokenCount") or 0),
        "output_tokens": int(out.get("outputTokenCount") or 0),
        "cache_read_tokens": int(inp.get("cacheReadInputTokenCount") or 0),
        "cache_write_tokens": int(inp.get("cacheWriteInputTokenCount") or 0),
        "error": bool(record.get("errorCode")),
    }


def _keys(s3: Any, bucket: str, prefix: str) -> Iterator[str]:
    token = None
    seen = 0
    while True:
        args: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix}
        if token:
            args["ContinuationToken"] = token
        page = s3.list_objects_v2(**args)
        for obj in page.get("Contents") or []:
            seen += 1
            if seen > MAX_KEYS:
                return
            yield obj["Key"]
        if not page.get("IsTruncated"):
            return
        token = page.get("NextContinuationToken")


def _records(data: bytes, key: str) -> Iterator[dict[str, Any]]:
    if key.endswith(".gz"):
        data = gzip.decompress(data)
    for raw in data.decode("utf-8", "replace").splitlines():
        text = raw.strip()
        if text:
            try:
                yield json.loads(text)
            except ValueError:
                continue


def import_logs(
    db: Session,
    gateway: AwsGateway,
    connection_id: uuid.UUID,
    *,
    days: int = 3,
    today: date | None = None,
) -> dict[str, Any]:
    set_tenant(db, bypass=True)
    conn = db.get(CloudConnection, connection_id)
    cfg = ((conn.settings or {}).get("bedrock_logs") or {}) if conn else {}
    if conn is None or conn.status != "active" or not conn.role_arn or not cfg.get("bucket"):
        return {}
    org_id, today = conn.org_id, today or utcnow().date()
    wanted = {today - timedelta(days=i) for i in range(days)}
    base = (cfg.get("prefix") or "").strip("/")
    root = f"{base + '/' if base else ''}AWSLogs/"
    try:
        session = gateway.assume(conn.role_arn, conn.external_id, f"ccradar-{org_id}")
        s3 = gateway.client(session, "s3", region="us-east-1")
        keys = [
            k
            for k in _keys(s3, cfg["bucket"], root)
            if "BedrockModelInvocationLogs" in k
            and (m := DAY.search(k))
            and date(int(m[1]), int(m[2]), int(m[3])) in wanted
        ]
        files = []
        for key in keys:
            buf = io.BytesIO()
            s3.download_fileobj(Bucket=cfg["bucket"], Key=key, Fileobj=buf)
            files.append((key, buf.getvalue()))
    except (AwsAccessError, Exception) as exc:
        conn.settings = {
            **(conn.settings or {}),
            "bedrock_logs": {**cfg, "error": f"{type(exc).__name__}: {str(exc)[:200]}"},
        }
        db.flush()
        return {"error": type(exc).__name__}
    set_tenant(db, org_id=org_id)
    org = db.get(Organization, org_id)
    prices = ai_usage.price_list(org)
    source = f"bedrock_logs:{conn.id}"
    lo = datetime.combine(min(wanted), datetime.min.time(), tzinfo=UTC)
    db.execute(delete(AiUsageEvent).where(AiUsageEvent.source == source, AiUsageEvent.at >= lo))
    rules = list(cfg.get("rules") or [])
    added = unpriced = 0
    for key, data in files:
        for record in _records(data, key):
            c, who = counts(record), attribute(record, rules)
            del record  # nothing else from the record is kept
            try:
                at = datetime.fromisoformat(str(c["at"]).replace("Z", "+00:00"))
            except ValueError:
                continue
            if at < lo:
                continue
            event = ai_usage.Event.model_construct(
                at=at,
                provider="bedrock",
                model=c["model"],
                input_tokens=c["input_tokens"],
                output_tokens=c["output_tokens"],
                cache_read_tokens=c["cache_read_tokens"],
                cache_write_tokens=c["cache_write_tokens"],
            )
            cost = ai_usage.price(prices, c["model"], event)
            unpriced += cost is None
            db.add(
                AiUsageEvent(
                    org_id=org_id,
                    at=at,
                    provider="bedrock",
                    model=(_ident(c["model"].split("/")[-1]) or "unknown"),
                    app=who["app"],
                    team=who["team"],
                    feature=who["feature"],
                    input_tokens=c["input_tokens"],
                    output_tokens=c["output_tokens"],
                    cache_read_tokens=c["cache_read_tokens"],
                    cache_write_tokens=c["cache_write_tokens"],
                    cost_usd=cost or 0.0,
                    cost_source="price list" if cost is not None else "unknown",
                    status="error" if c["error"] else "ok",
                    source=source,
                )
            )
            added += 1
    set_tenant(db, bypass=True)
    conn.settings = {
        **(conn.settings or {}),
        "bedrock_logs": {
            **cfg,
            "error": None,
            "read_at": utcnow().isoformat(),
            "files": len(files),
            "events": added,
        },
    }
    db.flush()
    return {"files": len(files), "events": added, "unpriced": unpriced}
