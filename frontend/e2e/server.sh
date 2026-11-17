#!/usr/bin/env bash
# Starts a real CloudCostRadar for the browser tests: a fresh SQLite database in a
# temporary folder, the built web app, local email sign-in and the demo organisation.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DATA="$(mktemp -d)"
trap 'rm -rf "$DATA"' EXIT
export CCRADAR_DEPLOYMENT_MODE=local CCRADAR_LOCAL_LOGIN=true
export CCRADAR_DATA_DIR="$DATA" CCRADAR_DATABASE_URL="sqlite:///$DATA/e2e.db"
export CCRADAR_UI_DIST_DIR="$ROOT/frontend/dist" CCRADAR_FX_PROVIDER=none CCRADAR_LOG_LEVEL=WARNING
# The identity customers' read-only AWS roles trust (a test value; nothing calls AWS).
export CCRADAR_AWS_PRINCIPAL_ARN="arn:aws:iam::999999999999:role/ccradar-e2e-collector"
CLI="$ROOT/backend/.venv/bin/ccradar"
"$CLI" migrate >/dev/null
"$CLI" demo seed >/dev/null
exec "$CLI" serve --host 127.0.0.1 --port "${E2E_PORT:-8765}"
