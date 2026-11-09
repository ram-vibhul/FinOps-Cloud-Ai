from __future__ import annotations

import json

from costlens.cli import main
from costlens.k8s import lab


def test_every_recorded_failure_is_diagnosed_first() -> None:
    """Regression guard: a change that makes the engine rank a known failure wrongly fails CI."""
    report = lab.run()
    misses = [r for r in report["results"] if not r["correct"]]
    assert misses == [], misses
    assert report["scenarios"] >= 12
    expected = {s.expected for s in lab.SCENARIOS}
    assert {"oom", "crash_loop", "config", "image_pull", "probes", "node", "dns", None} <= expected


def test_misses_are_reported_with_rank() -> None:
    wrong = lab.Scenario(
        "mislabelled",
        "dns",
        lab.SCENARIOS[0].pods,
        lab.SCENARIOS[0].events,
        usage=lab.SCENARIOS[0].usage,
    )
    report = lab.run([wrong])
    assert report["accuracy"] == 0 and report["results"][0]["top"] == "oom"
    assert report["results"][0]["rank"] is None


def test_cli_records_history_and_enforces_minimum(tmp_path, capsys) -> None:
    history = tmp_path / "lab" / "history.jsonl"
    assert main(["lab", "run", "--record", str(history), "--min", "1.0"]) == 0
    out = capsys.readouterr().out
    assert "accuracy: 12/12 = 100%" in out
    line = json.loads(history.read_text().splitlines()[0])
    assert line["accuracy"] == 1.0 and line["misses"] == [] and line["version"]
    main(["lab", "run", "--record", str(history)])
    assert len(history.read_text().splitlines()) == 2  # appended, tracked over time
