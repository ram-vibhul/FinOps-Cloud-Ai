"""The pull-request cost comment action (11.34), run against a local fake server."""

from __future__ import annotations

import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

ACTION = Path(__file__).resolve().parents[2] / "integrations" / "github-action"


def load():
    spec = importlib.util.spec_from_file_location("ccr_action", ACTION / "estimate.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def server():
    seen: list[tuple[str, dict, bytes]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers["Content-Length"]))
            seen.append((self.path, dict(self.headers), body))
            out = {"currency": "USD", "monthly_change": 412.5, "markdown": "| x | +412.50 |"}
            data = json.dumps(out).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: object) -> None:
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://localhost:{httpd.server_address[1]}", seen
    httpd.shutdown()


def test_action_inputs_never_inline_into_the_script() -> None:
    spec = yaml.safe_load((ACTION / "action.yml").read_text())
    (step,) = spec["runs"]["steps"]
    assert "${{" not in step["run"]  # inputs reach the script only through env (no injection)
    assert set(spec["inputs"]) >= {"base-url", "api-key", "plan", "fail-above"}


def test_estimate_summary_outputs_and_limit(server, tmp_path, monkeypatch) -> None:
    url, seen = server
    plan = tmp_path / "plan.json"
    plan.write_text('{"resource_changes": []}')
    out, summary = tmp_path / "out", tmp_path / "summary"
    env = {"CCRADAR_URL": url, "CCRADAR_API_KEY": "clk_test", "KIND": "terraform",
           "PLAN": str(plan), "BEFORE": "", "FAIL_ABOVE": "", "COMMENT": "false",
           "GITHUB_OUTPUT": str(out), "GITHUB_STEP_SUMMARY": str(summary)}  # fmt: skip
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    action = load()
    assert action.main() == 0
    path, headers, body = seen[0]
    assert path == "/api/v1/estimates/terraform" and headers["Authorization"] == "Bearer clk_test"
    assert body == plan.read_bytes()
    assert "change=412.5" in out.read_text() and "+412.50" in summary.read_text()
    monkeypatch.setenv("FAIL_ABOVE", "100")
    assert action.main() == 1
    monkeypatch.setenv("CCRADAR_URL", "http://example.com")
    assert action.main() == 1  # plain http only for localhost
