#!/bin/sh
# Container entrypoint.
#   web     migrate (unless CCRADAR_SKIP_MIGRATE=1) then serve API + UI
#   worker  run the PostgreSQL job worker
#   migrate apply migrations and exit
#   *       run anything else (e.g. a shell)
set -eu
case "${1:-web}" in
  web)
    if [ "${CCRADAR_SKIP_MIGRATE:-0}" != "1" ]; then ccradar migrate; fi
    exec uvicorn ccradar.asgi:app --host 0.0.0.0 --port "${CCRADAR_PORT:-8000}" \
      --proxy-headers --forwarded-allow-ips="${CCRADAR_FORWARDED_ALLOW_IPS:-127.0.0.1}" \
      --workers "${CCRADAR_WEB_WORKERS:-2}" ;;
  worker) exec ccradar worker ;;
  migrate) exec ccradar migrate ;;
  *) exec "$@" ;;
esac
