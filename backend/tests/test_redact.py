from __future__ import annotations

import pytest

from ccradar.core.redact import redact

# Fake tokens in each provider's format, assembled from pieces so that secret scanners
# (GitHub push protection) do not mistake this test file for a leak. They are not real.
GH = "gh" + "p_" + "aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789"
SLACK = "xo" + "xb-" + "123456789012-1234567890123-AbCdEfGhIjKlMnOpQrStUvWx"
STRIPE = "sk_" + "live_" + "51H8xYzAbCdEfGhIjKlMnOp"
GOOGLE = "AI" + "za" + "SyA-1234567890abcdefghijklmnopqrstu"
ANTHROPIC = "sk-" + "ant-" + "api03-AbCdEfGhIjKlMnOpQrStUvWx"

# Real-looking (fake) secrets as they appear in logs and Kubernetes events.
CORPUS = [
    ("postgres://app:S3cr3t!pass@db.shop.svc:5432/orders", "S3cr3t!pass"),
    ("mongodb+srv://admin:hunter2@cluster0.example.net/test", "hunter2"),
    (
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
        "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
        "eyJhbGciOiJIUzI1NiJ9",
    ),
    ("using key AKIAIOSFODNN7EXAMPLE for s3", "AKIAIOSFODNN7EXAMPLE"),
    ("aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "wJalrXUtnFEMI/K7MDENG"),
    (
        "token " + GH + " rejected",
        GH,
    ),
    (
        "SLACK_BOT_TOKEN=" + SLACK,
        SLACK[:17],
    ),
    ("stripe error for " + STRIPE, STRIPE),
    (
        "calling with " + GOOGLE,
        GOOGLE,
    ),
    ("ANTHROPIC_API_KEY=" + ANTHROPIC, ANTHROPIC[:12]),
    ('{"password": "correct horse battery", "user": "x"}', "correct horse"),
    ("DB_PASSWORD='p@55w0rd!' started", "p@55w0rd!"),
    ("client_secret: 8a7d6f5e4c3b2a1908f7e6d5c4b3a291", "8a7d6f5e4c3b2a1908f7e6d5c4b3a291"),
    ("Cookie: session_id=abcdef1234567890; path=/", "abcdef1234567890"),
    (
        "-----BEGIN RSA "
        + "PRIVATE KEY-----\nMIIEowIBAAKCAQEA1234\n-----END RSA "
        + "PRIVATE KEY-----",
        "MIIEowIBAAKCAQEA1234",
    ),
    (
        "sha 3f786850e387550fdab836ed7e6dc881de23001b3f786850e387550fdab836ed",
        "3f786850e387550fdab836ed7e6dc881de23001b",
    ),
    (
        "agent token ckt_0123456789abcdef0123456789abcdef_AbCdEfGhIjKlMnOpQrStUvWxYz",
        "AbCdEfGhIjKlMnOpQrStUvWxYz",
    ),
]


@pytest.mark.parametrize(("text", "secret"), CORPUS)
def test_secrets_are_removed(text: str, secret: str) -> None:
    out = redact(text)
    assert secret not in out
    assert "REDACTED" in out


def test_context_survives_for_debugging() -> None:
    out = redact("connect postgres://app:pw1234@db.shop.svc:5432/orders failed: timeout")
    assert out == "connect postgres://[REDACTED]@db.shop.svc:5432/orders failed: timeout"
    msg = "Back-off restarting failed container api in pod api-6c-0 (exit 137)"
    assert redact(msg) == msg  # ordinary Kubernetes messages are untouched
    assert redact("Memory cgroup out of memory: Killed process 4242 (java)") == (
        "Memory cgroup out of memory: Killed process 4242 (java)"
    )


def test_personal_data_and_optional_ips() -> None:
    text = "user ravi@acme.example called +91 98765 43210 from 10.20.3.4"
    out = redact(text)
    assert "ravi@acme.example" not in out and "98765" not in out and "10.20.3.4" in out
    assert "10.20.3.4" not in redact(text, ips=True)
    assert "ravi@acme.example" in redact(text, personal=False)
    assert redact(None) == "" and redact("") == ""


def test_agent_copy_of_the_rules_is_identical() -> None:
    """The agent cannot import the backend, so it carries a copy of the rules."""
    from ccradar_agent import redact as agent

    from ccradar.core import redact as server

    def rules(module) -> list[tuple[str, str, str, int]]:
        all_rules = [*module.SECRET_RULES, module.EMAIL, module.PHONE, module.IPV4]
        return [(r.name, r.pattern.pattern, r.replacement, r.pattern.flags) for r in all_rules]

    assert rules(agent) == rules(server)
    sample = "postgres://u:p4ssw0rd@db/x ravi@acme.example 10.1.2.3 AKIAIOSFODNN7EXAMPLE"
    assert agent.redact(sample, ips=True) == server.redact(sample, ips=True)


def test_server_redacts_event_messages_on_ingest(db) -> None:
    import uuid

    from sqlalchemy import select

    from ccradar.db.base import utcnow
    from ccradar.db.tenant import set_tenant
    from ccradar.k8s.service import ingest_events
    from ccradar.models import K8sCluster, K8sEvent
    from tests.helpers import make_org

    org_id, _ = make_org(db, "Acme", "owner@acme.com")
    with db.session() as s:
        set_tenant(s, org_id=org_id)
        c = K8sCluster(
            id=uuid.uuid4(), org_id=org_id, name="prod", token_hash="x" * 64, token_hint="x"
        )
        s.add(c)
        s.flush()
        now = utcnow()
        ingest_events(
            s,
            c,
            [
                {
                    "uid": "e",
                    "message": "login password=hunter22 failed",
                    "first_at": now,
                    "last_at": now,
                }
            ],
        )
        assert s.scalars(select(K8sEvent.message)).one() == "login password=[REDACTED] failed"


@pytest.mark.parametrize("chunk", ["x", "a1", "9", "ab.", "-", "password", "a@", "http:"])
def test_long_lines_are_fast(chunk: str) -> None:
    """No catastrophic backtracking: 1 MB of adversarial text redacts in well under a second."""
    import time

    text = chunk * (1_000_000 // len(chunk))
    started = time.perf_counter()
    redact(text, ips=True)
    assert time.perf_counter() - started < 1.0
