from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ccradar.config import Settings
from ccradar.core.ratelimit import MemoryLimiter, PostgresLimiter, Rule, cleanup_postgres_buckets
from ccradar.db import Database
from ccradar.jobs import InProcessRunner
from ccradar.main import create_app
from tests.conftest import POSTGRES_URL
from tests.helpers import make_org, sign_in_client


def app_with(settings: Settings, db: Database, **limits: int) -> TestClient:
    for name, value in limits.items():
        setattr(settings, f"rate_limit_{name}_per_minute", value)
    return TestClient(create_app(settings, db=db, runner=InProcessRunner()), follow_redirects=False)


def test_memory_limiter_windows() -> None:
    lim, rule = MemoryLimiter(), Rule("t", 2)
    assert lim.hit("k", rule, now=1000).allowed
    second = lim.hit("k", rule, now=1010)
    assert second.allowed and second.remaining == 0
    third = lim.hit("k", rule, now=1019)
    assert not third.allowed and third.reset_after == 1
    assert lim.hit("other", rule, now=1019).allowed  # separate identity
    assert lim.hit("k", rule, now=1020).allowed  # new window


def test_anonymous_limit_and_headers(settings: Settings, db: Database) -> None:
    with app_with(settings, db, anonymous=3) as c:
        first = c.get("/api/v1/meta")
        assert first.headers["RateLimit-Limit"] == "3"
        assert first.headers["RateLimit-Remaining"] == "2"
        c.get("/api/v1/meta")
        c.get("/api/v1/meta")
        blocked = c.get("/api/v1/meta")
        assert blocked.status_code == 429
        assert blocked.json()["code"] == "rate_limited"
        assert int(blocked.headers["Retry-After"]) >= 1
        # Health checks and non-API paths are never limited.
        assert c.get("/healthz").status_code == 200
        assert "RateLimit-Limit" not in c.get("/healthz").headers


def test_sign_in_endpoints_have_tighter_limit(settings: Settings, db: Database) -> None:
    with app_with(settings, db, auth=2) as c:
        statuses = [c.get("/api/v1/auth/login/google").status_code for _ in range(3)]
        assert statuses == [404, 404, 429]


def test_signed_in_users_counted_per_session(settings: Settings, db: Database) -> None:
    with app_with(settings, db, anonymous=1, user=3, write=1) as c:
        org_id, owner = make_org(db, "Acme", "o@acme.com")
        sign_in_client(c, db, owner, org_id)
        assert c.get("/api/v1/auth/me").status_code == 200
        assert c.get("/api/v1/auth/me").status_code == 200  # anonymous limit not used
        spec = {
            "kind": "oidc",
            "display_name": "X",
            "issuer": "https://sso.example.com",
            "client_id": "c",
            "client_secret": "s",
        }
        assert c.post("/api/v1/org/sso", json=spec).status_code == 201
        assert c.post("/api/v1/org/sso", json=spec).status_code == 429  # write limit
        assert c.get("/api/v1/auth/me").status_code == 429  # user limit (4th call)


def test_can_be_disabled(settings: Settings, db: Database) -> None:
    settings.rate_limit_enabled = False
    with app_with(settings, db, anonymous=1) as c:
        assert all(c.get("/api/v1/meta").status_code == 200 for _ in range(3))


@pytest.mark.skipif(not POSTGRES_URL, reason="needs PostgreSQL")
def test_postgres_limiter_shared_and_cleaned(db: Database) -> None:
    rule = Rule("pg", 2)
    a, b = PostgresLimiter(db.engine), PostgresLimiter(db.engine)  # two "workers"
    assert a.hit("k", rule, now=1000).allowed
    assert b.hit("k", rule, now=1001).allowed
    assert not a.hit("k", rule, now=1002).allowed
    assert b.hit("k", rule, now=1060).allowed  # next window resets
    assert cleanup_postgres_buckets(db.engine, older_than=0) >= 1
