# Kubernetes diagnosis lab

Two ways to score how often CostLens ranks the right cause first.

## Replay (offline, runs in CI)

Recorded shapes of real failures (OOM kill, crash loops, missing ConfigMap, image pull,
probe failures, node pressure, unschedulable pods, volume mounts, DNS, and a healthy
control) are fed through the real investigation engine:

```bash
backend/.venv/bin/costlens lab run --record lab/history.jsonl --min 1.0
```

`backend/tests/test_k8s_lab.py` fails the build if any scenario stops ranking first.

## Live (kind cluster)

Reproduces each failure on a local [kind](https://kind.sigs.k8s.io/) cluster with the real
agent, then asks CostLens for an investigation and scores the top-ranked cause.

Prerequisites: Docker, `kind`, `kubectl`, `helm`, and a local CostLens running with local
sign-in (`make dev-api`) reachable from the cluster at `http://host.docker.internal:8000`.

```bash
kind create cluster --name costlens-lab
# In CostLens: Settings -> Kubernetes -> connect "costlens-lab", copy the token
make image   # builds costlens-agent:dev
kind load docker-image costlens-agent:dev --name costlens-lab
helm upgrade --install costlens-agent deploy/helm/costlens-agent -n costlens --create-namespace \
  --set costlens.url=http://host.docker.internal:8000 --set costlens.allowHttp=true \
  --set costlens.token=<token> --set image.repository=costlens-agent --set image.tag=dev
backend/.venv/bin/python tools/k8s-lab/run.py --cluster costlens-lab --record lab/history.jsonl
kind delete cluster --name costlens-lab
```

Each scenario in `scenarios/` runs in its own namespace (`lab-<name>`); the expected
cause is in the first line of the file. Node pressure and DNS failures are replay-only:
kind cannot reproduce node memory pressure reliably, and DNS errors usually appear only
in application logs, not in Kubernetes events.
