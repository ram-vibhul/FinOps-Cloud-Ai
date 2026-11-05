from __future__ import annotations

import json
import logging

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from pydantic import BaseModel

from costlens.config import Settings
from costlens.core.errors import ConflictError
from costlens.core.logging import JsonFormatter, RequestIdFilter, request_id_var
from costlens.core.metrics import Counter, Histogram, Registry
from costlens.db import Database
from costlens.jobs import InProcessRunner
from costlens.main import create_app


class Item(BaseModel):
    name: str
    qty: int


def make_client(settings: Settings, db: Database) -> TestClient:
    app = create_app(settings, db=db, runner=InProcessRunner())
    router = APIRouter()

    @router.get("/api/v1/_test/boom")
    def boom() -> None:
        raise RuntimeError("secret internal detail")

    @router.get("/api/v1/_test/conflict")
    def conflict() -> None:
        raise ConflictError("Already exists", code="duplicate_name")

    @router.post("/api/v1/_test/items/{item_id}")
    def create(item_id: int, item: Item) -> dict[str, int]:
        return {"id": item_id}

    app.include_router(router)
    return TestClient(app)


# --- problem+json ---------------------------------------------------------


def test_not_found_is_problem_json(client: TestClient) -> None:
    response = client.get("/api/v1/nope")
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["status"] == 404
    assert body["title"] == "Not Found"
    assert body["code"] == "not_found"
    assert body["request_id"] == response.headers["X-Request-ID"]


def test_validation_error_lists_fields(settings: Settings, db: Database) -> None:
    with make_client(settings, db) as c:
        body = c.post("/api/v1/_test/items/1", json={"name": "x", "qty": "many"}).json()
    assert body["status"] == 422
    assert body["code"] == "validation_error"
    assert body["errors"][0]["loc"] == ["body", "qty"]


def test_app_error_keeps_code(settings: Settings, db: Database) -> None:
    with make_client(settings, db) as c:
        response = c.get("/api/v1/_test/conflict")
    assert response.status_code == 409
    assert response.json()["code"] == "duplicate_name"
    assert response.json()["detail"] == "Already exists"


def test_unhandled_error_hides_details_and_keeps_headers(
    settings: Settings, db: Database, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR, logger="costlens.errors"), make_client(settings, db) as c:
        response = c.get("/api/v1/_test/boom")
    assert response.status_code == 500
    body = response.json()
    assert body["code"] == "internal_error"
    assert "secret internal detail" not in response.text
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert any("unhandled error" in r.message for r in caplog.records)


# --- logging --------------------------------------------------------------


def test_json_formatter_includes_request_id_and_extra() -> None:
    record = logging.LogRecord("costlens.test", logging.INFO, __file__, 1, "hello %s", ("w",), None)
    record.status = 200
    token = request_id_var.set("req-1")
    try:
        RequestIdFilter().filter(record)
    finally:
        request_id_var.reset(token)
    entry = json.loads(JsonFormatter().format(record))
    assert entry["msg"] == "hello w"
    assert entry["level"] == "info"
    assert entry["request_id"] == "req-1"
    assert entry["status"] == 200


def test_access_log_has_route_template(
    settings: Settings, db: Database, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="costlens.access"), make_client(settings, db) as c:
        c.post("/api/v1/_test/items/42", json={"name": "x", "qty": 1})
    record = next(r for r in caplog.records if r.name == "costlens.access")
    assert record.route == "/api/v1/_test/items/{item_id}"  # type: ignore[attr-defined]
    assert record.status == 200  # type: ignore[attr-defined]


def test_log_format_auto() -> None:
    assert Settings(_env_file=None).effective_log_format == "text"  # type: ignore[call-arg]
    prod = Settings(
        _env_file=None,  # type: ignore[call-arg]
        deployment_mode="saas",
        secret_key="s",
        encryption_keys="k",
        database_url="postgresql://u@h/db",
    )
    assert prod.effective_log_format == "json"


# --- metrics --------------------------------------------------------------


def test_metrics_open_in_local_mode(client: TestClient) -> None:
    client.get("/healthz")
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    text = response.text
    assert 'costlens_http_requests_total{method="GET",route="/healthz",status="200"}' in text
    assert "costlens_http_request_duration_seconds_bucket" in text
    assert 'costlens_build_info{version="0.1.0",mode="local"} 1' in text


def test_metrics_requires_token_when_configured(settings: Settings, db: Database) -> None:
    from pydantic import SecretStr

    settings.metrics_token = SecretStr("t0ken")
    with TestClient(create_app(settings, db=db, runner=InProcessRunner())) as c:
        assert c.get("/metrics").status_code == 401
        assert c.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert c.get("/metrics", headers={"Authorization": "Bearer t0ken"}).status_code == 200


def test_metrics_hidden_in_production_without_token(settings: Settings, db: Database) -> None:
    from costlens.config import DeploymentMode

    settings.deployment_mode = DeploymentMode.SELF_HOSTED
    with TestClient(create_app(settings, db=db, runner=InProcessRunner())) as c:
        assert c.get("/metrics").status_code == 404
        assert "Strict-Transport-Security" in c.get("/healthz").headers


def test_registry_rendering() -> None:
    reg = Registry()
    c = reg.register(Counter("t_total", "Test.", ("k",)))
    h = reg.register(Histogram("t_seconds", "Test.", (), buckets=(0.1, 1.0)))
    c.inc(k='a"b\nc')
    c.inc(2, k="z")
    h.observe(0.05)
    h.observe(5)
    text = reg.render()
    assert 't_total{k="a\\"b\\nc"} 1' in text
    assert 't_total{k="z"} 2' in text
    assert 't_seconds_bucket{le="0.1"} 1' in text
    assert 't_seconds_bucket{le="+Inf"} 2' in text
    assert "t_seconds_count 2" in text
    assert h.count() == 2
    with pytest.raises(ValueError):
        c.inc(k="a", extra="x")
    with pytest.raises(ValueError):
        c.inc(-1, k="a")
    with pytest.raises(ValueError):
        reg.register(Counter("t_total", "dup"))


def test_job_metrics_recorded() -> None:
    from costlens.core.metrics import JOBS
    from costlens.jobs import JobRegistry, job

    reg = JobRegistry()
    job("metric_ok", into=reg)(lambda: None)
    before = JOBS.value(job="metric_ok", outcome="succeeded")
    InProcessRunner(reg).enqueue("metric_ok")
    assert JOBS.value(job="metric_ok", outcome="succeeded") == before + 1
