# Architecture

## One build, three ways to run

The same image serves every deployment, chosen with `CCRADAR_DEPLOYMENT_MODE`:

| Mode | Who runs it | Database | Billing / licence |
|---|---|---|---|
| `local` | A developer or a quick trial on a laptop | SQLite | Everything unlocked |
| `saas` | Yugiverse | PostgreSQL 17 | Plans, trials and subscriptions |
| `self_hosted` | The customer | PostgreSQL 17 | Offline Ed25519 licence key, else the free tier |

## Processes

```
browser ──HTTPS──▶ web (FastAPI + the built React UI)
                      │            ▲
                      ▼            │ in-cluster agent (Kubernetes) pushes usage and events
                 PostgreSQL ◀──── worker (Procrastinate jobs: imports, detectors, alerts)
                      ▲
                 migrate (one-shot, owner role)
```

- **web** (`ccradar serve`): the JSON API under `/api/v1`, the single-page app at `/`,
  health checks `/healthz` and `/readyz`, metrics `/metrics`, OpenAPI at `/docs`.
- **worker** (`ccradar worker`): background and scheduled jobs on PostgreSQL. With
  SQLite, jobs run inside the web process instead.
- **migrate** (`ccradar migrate`): Alembic migrations with the owner role, then grants the
  runtime role data access only.
- **agent**: a small read-only Python agent installed in each Kubernetes cluster with a
  Helm chart (`deploy/helm/cloudcostradar-agent`).

## Code layout (`backend/src/ccradar`)

| Package | What it holds |
|---|---|
| `api/` | HTTP routes, one module per area; dependencies in `api/deps.py` (auth, roles, plan features) |
| `accounts/`, `auth/` | Organisations, members, roles and custom roles, SSO (OIDC), SCIM, API keys, sessions |
| `clouds/` | AWS, Azure and Google Cloud clients, importers, advice, permission checks, fixes |
| `connectors/` | SaaS cost connectors (Snowflake, Databricks, Datadog, MongoDB Atlas) |
| `costs/` | The cost model, imports, explorer, anomalies, budgets, forecasts, reports |
| `findings/` | Savings detectors and their lifecycle |
| `k8s/` | Kubernetes costs, usage, health and investigations |
| `billing/` | Plans and limits, subscriptions, payment webhooks, licences |
| `core/` | Errors, logging, metrics, crypto, notifications and the inbox, outgoing webhooks, SSRF guard |
| `jobs/` | Job registry, runners and the scheduled jobs |
| `models/` | SQLAlchemy models; `migrations/` holds Alembic revisions |

The frontend (`frontend/`) is React 19 with Vite and Tailwind; it only talks to `/api/v1`.

## Tenancy

Every tenant table carries `org_id` and has a PostgreSQL row-level security policy. Each
request sets the organisation (and user) as transaction-local settings, and the runtime
database role cannot bypass RLS, so a bug in a query cannot leak another organisation's
rows. Tables a person may read across organisations (their memberships, their own
notifications) add a user-column clause to the policy. SQLite runs the same checks in
application code for development.

## Data flow

1. **Connect** a cloud: read-only role or service principal; secrets encrypted at rest.
2. **Import**: billing exports (AWS CUR 2.0 / FOCUS, Azure FOCUS exports, Google BigQuery
   export) or cost APIs, normalised to FOCUS-aligned daily facts per organisation.
3. **Analyse**: detectors produce findings (savings) with risk and effort; overlapping
   ideas share an overlap key and are counted once; the clouds' own recommendations are
   imported too.
4. **Notify**: one notification service puts events in the people's inboxes, emails those
   who asked, and sends to Slack/Teams channels and outgoing webhooks.
5. **Verify**: savings are checked against later bills; retention jobs delete old data.

## Jobs

Jobs are plain functions registered with `@job(name, queue, cron=…)`. Scheduled jobs
include imports every six hours, detectors daily, anomaly detection, budget checks,
the weekly digest, retention, subscription periods and webhook delivery.
