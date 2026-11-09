"""Error tracking (D.9): off without a DSN; reports carry no personal data or secrets."""

from __future__ import annotations

from typing import Any

import pytest
import sentry_sdk
from pydantic import SecretStr
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport
from sentry_sdk.utils import event_from_exception

from costlens.config import Settings
from costlens.core import error_tracking

DSN = "https://publickey@errors.example.invalid/1"


class Capture(Transport):
    events: list[dict[str, Any]] = []  # noqa: RUF012 - shared by the test on purpose

    def capture_envelope(self, envelope: Envelope) -> None:
        for item in envelope.items:
            if item.type == "event":
                Capture.events.append(item.payload.json or {})


def test_off_without_a_dsn(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[Any] = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: called.append(kw))
    assert error_tracking.configure(settings) is False and called == []


def test_reports_are_scrubbed(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    options: dict[str, Any] = {}
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: options.update(kw))
    settings.error_tracking_dsn = SecretStr(DSN)
    assert error_tracking.configure(settings) is True
    assert not options.get("send_default_pii") and options["traces_sample_rate"] == 0.0
    assert options["include_local_variables"] is False
    assert options["include_source_context"] is False

    Capture.events = []
    client = sentry_sdk.Client(**options, transport=Capture)
    scope = sentry_sdk.Scope(client=client)
    scope.set_user({"email": "asha@acme.com", "ip_address": "10.1.2.3"})
    scope.add_breadcrumb(message="signed in as asha@acme.com from 10.1.2.3", data={"token": "x"})
    secret = "AKIAIOSFODNN7EXAMPLE"  # an AWS access key id pattern
    try:
        raise RuntimeError(f"cannot import for ravi@acme.com with key {secret}")
    except RuntimeError as exc:
        event, hint = event_from_exception(exc, client_options=client.options)
        client.capture_event(event, hint=hint, scope=scope)
    client.flush()
    assert len(Capture.events) == 1
    event = Capture.events[0]
    text = str(event)
    for leaked in ("asha@acme.com", "ravi@acme.com", "10.1.2.3", secret, "'token'"):
        assert leaked not in text, leaked
    assert "user" not in event
    assert "RuntimeError" in text and "cannot import" in text  # still useful for fixing


def test_request_details_are_dropped() -> None:
    event = {
        "request": {
            "method": "POST",
            "url": "https://costlens.example.com/api/v1/x?token=abc",
            "headers": {"Authorization": "Bearer clk_secret", "Cookie": "s=1"},
            "cookies": {"s": "1"},
            "data": {"password": "p"},
            "query_string": "token=abc",
        },
        "extra": {"anything": 1},
    }
    out = error_tracking.scrub(event)
    assert out["request"] == {"method": "POST", "url": "https://costlens.example.com/api/v1/x"}
    assert "extra" not in out


def test_heartbeat(settings: Settings, db, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    import httpx

    from costlens.core import notify
    from costlens.core.storage import build_storage
    from costlens.jobs import system
    from costlens.jobs.context import JobContext, configure_jobs

    configure_jobs(JobContext(settings=settings, db=db, storage=build_storage(settings.data_dir)))
    beat = tmp_path / "beat"
    monkeypatch.setattr(system, "HEARTBEAT_FILE", str(beat))
    assert system.worker_healthy(path=str(beat)) is False  # never ran
    assert system.heartbeat() is False  # no URL configured, but the beat is recorded
    assert system.worker_healthy(path=str(beat)) is True
    beat.write_text(str(int(beat.read_text()) - 3600))
    assert system.worker_healthy(path=str(beat)) is False  # an hour old
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(notify, "_http", httpx.Client(transport=httpx.MockTransport(handler)))
    settings.heartbeat_url = "https://status.example.com/api/push/abc"
    assert system.heartbeat() is True
    assert str(seen[0].url) == "https://status.example.com/api/push/abc?status=up&msg=OK"

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(notify, "_http", httpx.Client(transport=httpx.MockTransport(down)))
    assert system.heartbeat() is False
