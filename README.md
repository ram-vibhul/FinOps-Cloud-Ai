# CloudCostRadar

Cloud cost management and Kubernetes operations for engineering and finance teams.
By Yugiverse. Proprietary — see [LICENSE](LICENSE).

> Engineering status, checklist, decisions and open questions: [TRACKER.md](TRACKER.md).

## Quick start (local, SQLite)

```bash
make setup
make dev-api   # http://127.0.0.1:8000  (API docs at /docs)
make dev-ui    # http://localhost:5173  (proxies /api to the API)
```

Or a single container: `docker compose -f deploy/compose/local.yaml up --build`.

## Self-hosted (PostgreSQL)

```bash
cp .env.example .env   # fill in secrets
docker compose up -d
```

## Checks

`make check` runs ruff, pyright, pytest, oxlint, tsc, vitest, the UI build and the
third-party licence check. Set `CCRADAR_TEST_DATABASE_URL=postgresql://…` to run the
backend tests against PostgreSQL.

## Configuration

All settings are environment variables prefixed `CCRADAR_` — see
`backend/src/ccradar/config.py` and `.env.example`.
