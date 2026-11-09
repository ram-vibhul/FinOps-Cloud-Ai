from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from costlens_ai import Usage, fingerprint

PROMPT = "Summarise this contract for ACME Corp: the secret price is 12345 rupees."
SALT = b"customer-side-salt"


class Fake:
    def __init__(self, answers: list[int | Exception] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any], dict[str, str]]] = []
        self.answers = list(answers or [])

    def __call__(self, url: str, body: bytes, headers: dict[str, str]) -> int:
        self.calls.append((url, json.loads(body), headers))
        answer = self.answers.pop(0) if self.answers else 202
        if isinstance(answer, Exception):
            raise answer
        return answer


def usage(fake: Fake, **kw: Any) -> Usage:
    return Usage("clai_test", "https://costlens.example/", salt=SALT, transport=fake, **kw)


def test_prompt_text_is_never_sent_only_a_salted_fingerprint() -> None:
    fake = Fake()
    u = usage(fake, app="checkout", team="payments team")  # space is cleaned
    event = u.record(
        "anthropic", "claude-sonnet-5-5", input_tokens=1200, output_tokens=300, prompt=PROMPT
    )
    u.flush()
    url, body, headers = fake.calls[0]
    raw = json.dumps(body)
    assert url == "https://costlens.example/api/v1/ingest/ai-usage"
    assert headers["Authorization"] == "Bearer clai_test"
    for secret in ("contract", "ACME", "12345", "rupees", "Summarise"):
        assert secret not in raw
    assert event["fingerprint"] == fingerprint(PROMPT, SALT) and len(event["fingerprint"]) == 32
    assert body["events"][0]["team"] == "payments-team" and body["events"][0]["app"] == "checkout"
    # Same prompt with different spacing -> same fingerprint; another salt -> different one.
    assert fingerprint("  " + PROMPT.replace(" ", "\n  "), SALT) == event["fingerprint"]
    assert fingerprint(PROMPT, b"other") != event["fingerprint"]
    # The TypeScript SDK test checks the same value, so both SDKs match repeated prompts.
    assert fingerprint("hello world", SALT) == "3fba58ec5da16dca8541127849e4f65e"


def test_names_cannot_carry_sentences() -> None:
    fake = Fake()
    u = usage(fake)
    e = u.record("openai", "gpt-4o", feature="Please summarise: " + PROMPT)
    assert " " not in e["feature"] and len(e["feature"]) <= 100
    allowed = {"at", "provider", "model", "input_tokens", "output_tokens", "cache_read_tokens",
               "cache_write_tokens", "retry", "status", "feature"}  # fmt: skip
    assert set(e) <= allowed


def test_response_helpers() -> None:
    fake = Fake()
    u = usage(fake)
    anthropic = SimpleNamespace(
        model="claude-opus-5-5",
        usage=SimpleNamespace(
            input_tokens=100,
            output_tokens=50,
            cache_read_input_tokens=900,
            cache_creation_input_tokens=10,
        ),
    )
    a = u.from_anthropic(anthropic, feature="chat", latency_ms=812)
    assert (a["provider"], a["input_tokens"], a["cache_read_tokens"], a["cache_write_tokens"]) == (
        "anthropic",
        100,
        900,
        10,
    )
    assert a["latency_ms"] == 812
    openai = SimpleNamespace(
        model="gpt-4o-mini",
        usage=SimpleNamespace(
            prompt_tokens=1000,
            completion_tokens=20,
            prompt_tokens_details=SimpleNamespace(cached_tokens=600),
        ),
    )
    o = u.from_openai(openai, retry=True)
    assert (o["input_tokens"], o["cache_read_tokens"], o["output_tokens"], o["retry"]) == (
        400,
        600,
        20,
        True,
    )


def test_batching_retries_and_never_raising() -> None:
    fake = Fake([ConnectionError("down"), 503, 202])
    u = usage(fake, batch_size=2)
    u.record("anthropic", "m")
    assert fake.calls == []
    u.record("anthropic", "m")  # batch full -> flush -> network error, kept for later
    assert len(fake.calls) == 1
    assert u.flush() is False  # 503, kept
    assert u.flush() is True  # 202
    assert [len(c[1]["events"]) for c in fake.calls] == [2, 2, 2]
    rejected = Fake([401])
    u2 = usage(rejected)
    u2.record("anthropic", "m")
    assert u2.flush() is True  # a rejected batch is dropped, not retried forever


def test_needs_a_usage_key_and_https() -> None:
    with pytest.raises(ValueError, match="clai_"):
        Usage("sk-123", "https://x")
    from costlens_ai import _urllib_transport

    with pytest.raises(ValueError, match="https"):
        _urllib_transport("http://evil.example/x", b"{}", {})
