"""Live diagnosis lab: apply each scenario to a kind cluster, wait for the agent to
report, ask CloudCostRadar to investigate, and score the top-ranked cause.

Usage: python tools/k8s-lab/run.py --cluster ccradar-lab [--url http://localhost:8000]
       [--wait 180] [--record lab/history.jsonl]
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

HERE = Path(__file__).parent


KUBECTL = shutil.which("kubectl") or "kubectl"


def kubectl(*args: str, check: bool = True) -> str:
    # Fixed argument lists, no shell: names come from this folder's scenario files.
    done = subprocess.run([KUBECTL, *args], text=True, capture_output=True, check=check)  # noqa: S603
    return done.stdout


def expected_of(path: Path) -> str:
    first = path.read_text().splitlines()[0]
    return first.split(":", 1)[1].strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cluster", required=True, help="cluster name as connected in CloudCostRadar")
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--email", default="lab@example.com", help="local sign-in account")
    ap.add_argument("--wait", type=int, default=180, help="seconds for failures to show")
    ap.add_argument("--record", help="append the score to this JSON-lines file")
    args = ap.parse_args()

    http = httpx.Client(base_url=args.url, timeout=30.0)
    http.post("/api/v1/auth/local", json={"email": args.email}).raise_for_status()
    me = http.get("/api/v1/auth/me").json()
    http.headers["x-csrf-token"] = me.get("csrf_token", "")
    cluster = next(c for c in http.get("/api/v1/clusters").json() if c["name"] == args.cluster)

    scenarios = sorted((HERE / "scenarios").glob("*.yaml"))
    for path in scenarios:
        ns = f"lab-{path.stem}"
        kubectl("create", "namespace", ns, check=False)  # may exist from an earlier run
        kubectl("apply", "-n", ns, "-f", str(path))
    print(f"waiting {args.wait}s for failures to develop and the agent to report…")
    time.sleep(args.wait)

    results = []
    for path in scenarios:
        ns = f"lab-{path.stem}"
        r = http.post(
            "/api/v1/k8s/investigations/run",
            json={
                "cluster_id": cluster["id"],
                "namespace": ns,
                "kind": "Deployment",
                "name": "app",
                "hours": 1,
            },
        ).json()
        top = r["causes"][0]["check"] if r.get("causes") else None
        expected = expected_of(path)
        ok = top == expected
        results.append({"scenario": path.stem, "expected": expected, "top": top, "correct": ok})
        print(f"{path.stem:<20} expected {expected:<14} got {top!s:<14} {'ok' if ok else 'MISS'}")
        kubectl("delete", "namespace", ns, "--wait=false", check=False)

    correct = sum(r["correct"] for r in results)
    print(f"live accuracy: {correct}/{len(results)}")
    if args.record:
        Path(args.record).parent.mkdir(parents=True, exist_ok=True)
        with open(args.record, "a", encoding="utf-8") as f:
            line = {
                "at": datetime.now(UTC).isoformat(),
                "mode": "live",
                "correct": correct,
                "accuracy": round(correct / len(results), 3),
                "scenarios": len(results),
                "misses": [r["scenario"] for r in results if not r["correct"]],
            }
            f.write(json.dumps(line) + "\n")
    return 0 if correct == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
