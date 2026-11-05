#!/bin/sh
# Runs once, when the PostgreSQL data directory is first created.
# Creates the runtime role CostLens connects as. It is NOT a superuser and does
# NOT have BYPASSRLS, so row-level security isolates tenants. `costlens migrate`
# (run as the owner, $POSTGRES_USER) grants it data access.
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v app_password="$COSTLENS_DB_APP_PASSWORD" <<'SQL'
CREATE ROLE costlens_app LOGIN PASSWORD :'app_password' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
GRANT CONNECT ON DATABASE costlens TO costlens_app;
SQL
