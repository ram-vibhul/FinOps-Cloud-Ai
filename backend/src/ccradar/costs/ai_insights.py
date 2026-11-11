"""Why AI spend changed, and where tokens are wasted (from SDK usage events).

**Change**: the last N days against the N before. The total change is split by model,
team, feature and app. For each model the change is split into a *volume* effect (more
or fewer tokens at the old price per token) and a *price/mix* effect (the price per
token changed: a price list change, or a different mix of input, output and cached
tokens). The two always add up to the model's change.

**Waste** (last 30 days, scaled to a month):
- repeated prompts: the same fingerprint many times without cache reads → prompt
  caching would bill the repeats at about a tenth of the input price;
- oversized context: large inputs for tiny outputs → trim or cache the context;
- expensive model for small jobs: top-tier models on short prompts and short answers →
  the cheaper sibling (same provider) usually does;
- retries and errors: money spent on calls that were retried or failed.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ccradar.costs import ai_usage
from ccradar.db.base import utcnow
from ccradar.models import AiUsageEvent, Organization

REPEAT_MIN = 20  # same prompt fingerprint at least this often in the window
OVERSIZED_INPUT = 20_000
OVERSIZED_RATIO = 50  # input tokens per output token
SMALL_INPUT, SMALL_OUTPUT = 2_000, 300
MIN_SAVING = 5.0  # USD a month: smaller items are noise
CHEAPER = {
    "claude-fable": "claude-opus-5-5",
    "claude-opus": "claude-sonnet-5-5",
    "gpt-4.1": "gpt-4.1-mini",
    "gpt-4o": "gpt-4o-mini",
}
EXCLUDE_CHEAPER = ("mini", "nano", "haiku")
Prices = dict[str, tuple[float, float, float | None, float | None]]


def _window(days: int, end: datetime) -> tuple[datetime, datetime]:
    return end - timedelta(days=days), end


def _events(db: Session, lo: datetime, hi: datetime) -> list[AiUsageEvent]:
    return list(db.scalars(select(AiUsageEvent).where(AiUsageEvent.at >= lo, AiUsageEvent.at < hi)))


def _tokens(e: AiUsageEvent) -> int:
    return e.input_tokens + e.output_tokens + e.cache_read_tokens + e.cache_write_tokens


def explain(
    db: Session, org_id: uuid.UUID, days: int = 7, now: datetime | None = None
) -> dict[str, Any]:
    now = now or utcnow()
    cur_lo, cur_hi = _window(days, now)
    prev_lo, prev_hi = cur_lo - timedelta(days=days), cur_lo
    cur, prev = _events(db, cur_lo, cur_hi), _events(db, prev_lo, prev_hi)

    def by(events: list[AiUsageEvent], key: str) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for e in events:
            k = f"{e.provider}/{e.model}" if key == "model" else (getattr(e, key) or "(none)")
            out[k]["cost"] += e.cost_usd
            out[k]["tokens"] += _tokens(e)
            out[k]["input"] += e.input_tokens
            out[k]["output"] += e.output_tokens
            out[k]["requests"] += 1
        return out

    total_now, total_before = sum(e.cost_usd for e in cur), sum(e.cost_usd for e in prev)
    drivers: dict[str, list[dict[str, Any]]] = {}
    for dim in ("model", "team", "feature", "app"):
        a, b = by(cur, dim), by(prev, dim)
        rows = []
        for k in set(a) | set(b):
            now_c, before_c = a.get(k, {}).get("cost", 0.0), b.get(k, {}).get("cost", 0.0)
            row: dict[str, Any] = {
                "key": k,
                "now": round(now_c, 2),
                "before": round(before_c, 2),
                "change": round(now_c - before_c, 2),
                "tokens_now": int(a.get(k, {}).get("tokens", 0)),
                "tokens_before": int(b.get(k, {}).get("tokens", 0)),
            }
            if dim == "model":
                t_now, t_before = row["tokens_now"], row["tokens_before"]
                unit_before = before_c / t_before if t_before else (now_c / t_now if t_now else 0.0)
                volume = (t_now - t_before) * unit_before
                row["volume_effect"] = round(volume, 2)
                row["price_effect"] = round((now_c - before_c) - volume, 2)
            rows.append(row)
        drivers[dim] = sorted((r for r in rows if r["change"]), key=lambda r: -abs(r["change"]))[:5]
    sentences = []
    change = total_now - total_before
    if total_before or total_now:
        word = "up" if change > 0 else "down" if change < 0 else "unchanged"
        sentences.append(
            f"AI spend reported by your applications was {total_now:,.2f} USD in the last "
            f"{days} days, {word}{f' {abs(change):,.2f} USD' if change else ''} from "
            f"{total_before:,.2f} USD."
        )
    for m in drivers["model"][:2]:
        parts = []
        if abs(m["volume_effect"]) >= 0.01:
            more = "more" if m["volume_effect"] > 0 else "fewer"
            parts.append(f"{more} tokens ({m['volume_effect']:+,.2f})")
        if abs(m["price_effect"]) >= 0.01:
            parts.append(f"price per token or token mix ({m['price_effect']:+,.2f})")
        sentences.append(
            f"{m['key']}: {m['change']:+,.2f} USD"
            + (f", from {' and '.join(parts)}." if parts else ".")
        )
    for dim in ("team", "feature"):
        top = drivers[dim][:1]
        if top and top[0]["key"] != "(none)":
            sentences.append(
                f"Biggest {dim} change: {top[0]['key']} ({top[0]['change']:+,.2f} USD)."
            )
    return {
        "days": days,
        "now": round(total_now, 2),
        "before": round(total_before, 2),
        "change": round(change, 2),
        "drivers": drivers,
        "sentences": sentences,
    }


@dataclass
class Waste:
    kind: str  # repeated_prompts | oversized_context | expensive_model | retries
    key: str  # what it is about: app/feature/model
    title: str
    detail: str
    saving_monthly: float
    requests: int


def _cheaper(model: str) -> str | None:
    m = model.lower()
    if any(x in m for x in EXCLUDE_CHEAPER):
        return None
    return next((v for k, v in CHEAPER.items() if m.startswith(k)), None)


def _where(e: AiUsageEvent) -> str:
    return "/".join(x for x in (e.app, e.feature) if x) or f"{e.provider}/{e.model}"


def _cost_at(
    prices: Prices, model: str, e: AiUsageEvent, *, input_only: bool = False
) -> float | None:
    event = ai_usage.Event(
        at=e.at,
        provider=e.provider,
        model=model,
        input_tokens=e.input_tokens,
        output_tokens=0 if input_only else e.output_tokens,
        cache_read_tokens=0 if input_only else e.cache_read_tokens,
        cache_write_tokens=0 if input_only else e.cache_write_tokens,
    )
    return ai_usage.price(prices, model, event)


def _repeated(events: list[AiUsageEvent], prices: Prices, days: int) -> list[Waste]:
    repeats: dict[tuple[str, str], list[AiUsageEvent]] = defaultdict(list)
    for e in events:
        if e.fingerprint and not e.cache_read_tokens:
            repeats[(_where(e), e.fingerprint)].append(e)
    grouped: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for (loc, _fp), es in repeats.items():
        if len(es) >= REPEAT_MIN:
            costs = [_cost_at(prices, e.model, e, input_only=True) or 0.0 for e in es[1:]]
            grouped[loc].append((len(es), sum(costs) * (1 - ai_usage.CACHE_READ_SHARE)))
    out = []
    for loc, items in grouped.items():
        n = sum(c for c, _ in items)
        out.append(
            Waste(
                "repeated_prompts",
                loc,
                f"Cache repeated prompts in {loc}",
                f"{len(items)} prompt(s) were sent {n} times in {days} days with no cache reads. "
                "Prompt caching bills repeats at about a tenth of the input price.",
                sum(s for _, s in items),
                n,
            )
        )
    return out


def _oversized(events: list[AiUsageEvent]) -> list[Waste]:
    big: dict[str, list[AiUsageEvent]] = defaultdict(list)
    for e in events:
        if e.input_tokens >= OVERSIZED_INPUT and e.input_tokens >= OVERSIZED_RATIO * max(
            e.output_tokens, 1
        ):
            big[_where(e)].append(e)
    return [
        Waste(
            "oversized_context",
            loc,
            f"Trim or cache the context sent by {loc}",
            f"{len(es)} request(s) sent about {sum(e.input_tokens for e in es) // len(es):,} input "
            f"tokens for answers under {max(e.output_tokens for e in es):,} tokens.",
            sum(e.cost_usd for e in es) * 0.5,  # trimming or caching often halves it: a rough guide
            len(es),
        )
        for loc, es in big.items()
    ]


def _expensive(events: list[AiUsageEvent], prices: Prices) -> list[Waste]:
    small: dict[tuple[str, str, str], list[AiUsageEvent]] = defaultdict(list)
    for e in events:
        target = _cheaper(e.model)
        if target and e.input_tokens <= SMALL_INPUT and e.output_tokens <= SMALL_OUTPUT:
            small[(_where(e), e.model, target)].append(e)
    out = []
    for (loc, model, target), es in small.items():
        cheaper = sum(_cost_at(prices, target, e) or e.cost_usd for e in es)
        out.append(
            Waste(
                "expensive_model",
                loc,
                f"Try {target} instead of {model} in {loc}",
                f"{len(es)} short request(s) (under {SMALL_INPUT:,} tokens in and {SMALL_OUTPUT} "
                f"out) used {model}. Check quality on a sample before switching.",
                sum(e.cost_usd for e in es) - cheaper,
                len(es),
            )
        )
    return out


def _retries(events: list[AiUsageEvent]) -> list[Waste]:
    failed: dict[str, list[AiUsageEvent]] = defaultdict(list)
    for e in events:
        if e.retry or e.status == "error":
            failed[_where(e)].append(e)
    return [
        Waste(
            "retries",
            loc,
            f"Reduce retries and failed calls in {loc}",
            f"{len(es)} request(s) were retries or errors. Check timeouts, rate limits and "
            "max-token limits.",
            sum(e.cost_usd for e in es),
            len(es),
        )
        for loc, es in failed.items()
    ]


def waste(
    db: Session, org_id: uuid.UUID, days: int = 30, now: datetime | None = None
) -> list[Waste]:
    now = now or utcnow()
    lo, hi = _window(days, now)
    events = _events(db, lo, hi)
    if not events:
        return []
    prices = ai_usage.price_list(db.get(Organization, org_id))
    found = (
        _repeated(events, prices, days)
        + _oversized(events)
        + _expensive(events, prices)
        + _retries(events)
    )
    out = []
    for w in found:
        w.saving_monthly = round(w.saving_monthly * 30 / days, 2)
        if w.saving_monthly >= MIN_SAVING:
            out.append(w)
    return sorted(out, key=lambda w: -w.saving_monthly)


def as_utc(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=UTC)
