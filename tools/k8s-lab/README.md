# Kubernetes diagnosis lab

Two ways to score how often CloudCostRadar ranks the right cause first.

## Replay (offline, runs in CI)

Recorded shapes of real failures (OOM kill, crash loops, missing ConfigMap, image pull,
probe failures, node pressure, unschedulable pods, volume mounts, DNS, and a healthy
control) are fed through the real investigation engine:

```bash
backend/.venv/bin/ccradar lab run --record lab/history.jsonl --min 1.0
```

`backend/tests/test_k8s_lab.py` fails the build if any scenario stops ranking first.

## Live (kind cluster)

Reproduces each failure on a local [kind](https://kind.sigs.k8s.io/) cluster with the real
agent, then asks CloudCostRadar for an investigation and scores the top-ranked cause.

Prerequisites: Docker, `kind`, `kubectl`, `helm`, and a local CloudCostRadar running with local
sign-in (`make dev-api`) reachable from the cluster at `http://host.docker.internal:8000`.

```bash
kind create cluster --name ccradar-lab
# In CloudCostRadar: Settings -> Kubernetes -> connect "ccradar-lab", copy the token
make image   # builds cloudcostradar-agent:dev
kind load docker-image cloudcostradar-agent:dev --name ccradar-lab
helm upgrade --install cloudcostradar-agent deploy/helm/cloudcostradar-agent -n ccradar --create-namespace \
  --set ccradar.url=http://host.docker.internal:8000 --set ccradar.allowHttp=true \
  --set ccradar.token=<token> --set image.repository=cloudcostradar-agent --set image.tag=dev
backend/.venv/bin/python tools/k8s-lab/run.py --cluster ccradar-lab --record lab/history.jsonl
kind delete cluster --name ccradar-lab
```

Each scenario in `scenarios/` runs in its own namespace (`lab-<name>`); the expected
cause is in the first line of the file. Node pressure and DNS failures are replay-only:
kind cannot reproduce node memory pressure reliably, and DNS errors usually appear only
in application logs, not in Kubernetes events.
