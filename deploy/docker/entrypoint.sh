#!/bin/sh
# Container entrypoint.
#   web     migrate (unless COSTLENS_SKIP_MIGRATE=1) then serve API + UI
#   worker  run the PostgreSQL job worker
#   migrate apply migrations and exit
#   *       run anything else (e.g. a shell)
set -eu
case "${1:-web}" in
  web)
    if [ "${COSTLENS_SKIP_MIGRATE:-0}" != "1" ]; then costlens migrate; fi
    exec uvicorn costlens.asgi:app --host 0.0.0.0 --port "${COSTLENS_PORT:-8000}" \
      --proxy-headers --forwarded-allow-ips="${COSTLENS_FORWARDED_ALLOW_IPS:-127.0.0.1}" \
      --workers "${COSTLENS_WEB_WORKERS:-2}" ;;
  worker) exec costlens worker ;;
  migrate) exec costlens migrate ;;
  *) exec "$@" ;;
esac
