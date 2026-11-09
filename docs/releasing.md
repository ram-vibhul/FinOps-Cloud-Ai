# Development and releasing

## Local development

```bash
make setup      # Python venv in backend/.venv, editable install, npm ci
make dev-api    # migrate the SQLite database, run the API with local email sign-in
make dev-ui     # Vite dev server on http://localhost:5173
```

Sign in with any email address (local mode only). Settings → General can create the demo
organisation with sample data.

## Checks

```bash
make check      # lint, type checks, all tests (SQLite), UI build, notices and website checks
make test-pg    # the backend tests on PostgreSQL 17 in Docker (row-level security)
make pg-stop    # remove the PostgreSQL test container
```

Run both before finishing a change. `make test-pg` reuses its container; after changing
an existing migration in place, run `make pg-stop` first so the database is rebuilt.

Generated files with a `--check` in `make check`:

| File | Generator |
|---|---|
| `THIRD_PARTY_NOTICES.md` | `tools/third_party_notices.py` (also refuses strong copyleft licences) |
| `website/pricing.html` plans table | `tools/website_plans.py` |
| `docs/configuration.md` | `tools/config_reference.py` |
| `website/changelog.html` | `tools/changelog.py` (from `release-notes/`) |

## Database migrations

1. Change the models in `backend/src/costlens/models`.
2. Generate a revision against a fresh SQLite database migrated to the current head:

   ```bash
   cd backend
   COSTLENS_DATABASE_URL=sqlite:///./mig.db .venv/bin/costlens migrate
   COSTLENS_DATABASE_URL=sqlite:///./mig.db .venv/bin/alembic revision --autogenerate \
     --rev-id 00NN -m "what changed"
   ```

3. Edit the revision:
   - new tenant tables: enable row-level security after the generated commands, on
     PostgreSQL only (`enable_tenant_rls_sql(table)` from `costlens.db.rls`);
   - encrypted columns: use `sa.String` (the encryption is in the model);
   - new NOT NULL columns on existing tables: add a `server_default`.
4. New time-series tables must register a retention policy (`privacy/retention.py`).
5. Dropping or retyping anything, or deleting rows, in `upgrade()` needs a
   `# destructive-ok: <reason>` comment there (`test_migration_safety.py` enforces it),
   and the release notes must tell self-hosted customers to back up first.

## Continuous integration

`.github/workflows/ci.yml` runs on pushes: backend tests on SQLite and PostgreSQL, the
frontend checks, licence and generated-file checks, and an image smoke test.

## Releasing

1. Update the version, write `release-notes/<version>.md` (change its first line from
   `unreleased` to the date), run `tools/changelog.py`, and make sure CI is green.
2. Push a tag `vMAJOR.MINOR.PATCH`.
3. `.github/workflows/release.yml` builds multi-architecture images (amd64, arm64),
   pushes them to GHCR, attaches an SBOM and build provenance, and signs them with
   cosign (keyless).
4. Self-hosted customers update with `docker compose pull && docker compose up -d`;
   migrations run before the app starts.

Licence keys for customers are issued with `tools/licence-issuer` on Yugiverse's own
machine (see its docstring); it is never part of a release.
