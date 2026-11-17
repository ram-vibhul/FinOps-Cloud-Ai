#!/usr/bin/env bash
# Release smoke test (11.1): the full Compose stack (migrate, web, worker, PostgreSQL)
# from an image, as a customer would run it. Uses a throw-away settings file and its own
# Compose project, so it never touches your .env or your running containers.
#
#   tools/smoke.sh                 # build the image, then test it
#   SMOKE_IMAGE=ccradar:ci tools/smoke.sh   # test an image that is already built
#
# Fails if the stack does not become ready, the worker is not healthy, any scheduled job
# fails, or a backup cannot be made and verified.
set -euo pipefail
cd "$(dirname "$0")/.."
PROJECT=ccradar-smoke
PORT="${SMOKE_PORT:-18080}"
IMAGE="${SMOKE_IMAGE:-ccradar:smoke}"
WORK="$(mktemp -d)"
compose() { docker compose -p "$PROJECT" --env-file "$WORK/env" "$@"; }
cleanup() {
  compose logs --tail 40 >"$WORK/logs.txt" 2>&1 || true
  compose down -v >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

if [ -z "${SMOKE_IMAGE:-}" ]; then
  echo "building $IMAGE"
  docker build -q -t "$IMAGE" . >/dev/null
fi
secret() { python3 -c "import secrets; print(secrets.token_hex(24))"; }
cat >"$WORK/env" <<ENV
CCRADAR_IMAGE=$IMAGE
CCRADAR_ENV_FILE=$WORK/env
CCRADAR_HTTP_PORT=$PORT
CCRADAR_DEPLOYMENT_MODE=self_hosted
CCRADAR_BASE_URL=http://localhost:$PORT
CCRADAR_SECRET_KEY=$(secret)
CCRADAR_ENCRYPTION_KEYS=$(python3 -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())")
POSTGRES_PASSWORD=$(secret)
CCRADAR_DB_APP_PASSWORD=$(secret)
ENV

fail() { echo "SMOKE FAILED: $*" >&2; compose logs --tail 80 >&2 || true; exit 1; }

compose up -d --no-build
for _ in $(seq 1 90); do curl -fs "localhost:$PORT/readyz" >/dev/null && break; sleep 2; done
curl -fs "localhost:$PORT/readyz" >/dev/null || fail "web never became ready"
curl -fs "localhost:$PORT/" | grep -q '<div id="root">' || fail "the web app is not served"
echo "web ready"

# Scheduled jobs: the worker runs the due ones as it starts (heartbeat among them).
for _ in $(seq 1 60); do
  compose logs worker 2>/dev/null | grep -q "system.heartbeat.*ended with status: Success" && break
  sleep 2
done
compose logs worker | grep -q "system.heartbeat.*ended with status: Success" || fail "the heartbeat job never ran"
if compose logs worker | grep -q "ended with status: Error"; then
  fail "a scheduled job failed: $(compose logs worker | grep -o '[a-z_.]*\[[0-9]*\]([^)]*) ended with status: Error' | head -3)"
fi
compose exec -T worker ccradar worker-health >/dev/null || fail "worker-health says the worker is not healthy"
echo "worker healthy; scheduled jobs succeed"

compose run --rm -T migrate ccradar backup create /data/smoke.tar.gz >/dev/null 2>&1 || fail "backup failed"
compose run --rm -T migrate ccradar backup verify /data/smoke.tar.gz >/dev/null || fail "backup does not verify"
echo "backup created and verified"
echo "SMOKE PASSED"
