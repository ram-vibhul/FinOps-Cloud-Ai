from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from costlens.config import Settings
from costlens.db import Database
from costlens.jobs import InProcessRunner
from costlens.main import create_app


def test_healthz(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_checks_database(client: TestClient) -> None:
    assert client.get("/readyz").json() == {"status": "ok"}


def test_readyz_reports_unavailable_database(client: TestClient) -> None:
    client.app.state.db.ping = lambda: False  # type: ignore[attr-defined]
    response = client.get("/readyz")
    assert response.status_code == 503


def test_meta_is_public_and_non_sensitive(client: TestClient) -> None:
    body = client.get("/api/v1/meta").json()
    assert body["product"] == "CostLens"
    assert body["deployment_mode"] == "local"
    assert body["ai_enabled"] is False
    assert "database_url" not in body


def test_security_headers(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert len(response.headers["X-Request-ID"]) == 16
    assert "Strict-Transport-Security" not in response.headers  # local mode


def test_request_id_is_echoed_when_sane(client: TestClient) -> None:
    response = client.get("/healthz", headers={"X-Request-ID": "abc-123"})
    assert response.headers["X-Request-ID"] == "abc-123"
    response = client.get("/healthz", headers={"X-Request-ID": "x" * 200})
    assert response.headers["X-Request-ID"] != "x" * 200


def test_openapi_available(client: TestClient) -> None:
    assert client.get("/openapi.json").json()["info"]["title"] == "CostLens API"


def test_spa_served_with_fallback(tmp_path: Path, settings: Settings, db: Database) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("nope")
    settings.ui_dist_dir = dist
    app = create_app(settings, db=db, runner=InProcessRunner())
    with TestClient(app) as client:
        assert client.get("/").text == "<html>app</html>"
        assert client.get("/costs/daily").text == "<html>app</html>"
        assert client.get("/assets/app.js").text == "console.log(1)"
        assert client.get("/../secret.txt").text != "nope"
        assert client.get("/api/v1/unknown").status_code == 404
        assert client.get("/healthz").json() == {"status": "ok"}
