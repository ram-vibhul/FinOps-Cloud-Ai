# CostLens — engineering tracker

Single source of truth for the build: stack, checklist, design notes, open
questions and the dated log. **Each session: read this file, take the next
unchecked item (skip `BLOCKED`), design briefly, build with tests, run
`make check`, tick the item, add a log entry.**

Legend: `[ ]` todo · `[x]` done · `[~]` partly done (see note) · `BLOCKED(Qn)` waiting on open question *n*.

---

## 1. Product

- **Name: CostLens** ("a lens on your cloud costs") — by **Yugiverse**, a sole
  proprietorship in Tamil Nadu, India. Chosen by the owner on 2026-11-05 (English
  name for a worldwide market); trademark/domain checks pending, see Q1.
- Cloud cost management (FinOps) + Kubernetes operations for engineering and finance teams.
- Editions from one build: **SaaS** (hosted by Yugiverse), **self-hosted** (customer
  runs it, licence key), **local** (laptop / quick trial, SQLite).
- Config prefix: `COSTLENS_` (all settings are environment variables).

## 2. Stack and why

| Layer | Choice (pinned) | Why |
|---|---|---|
| Language | Python 3.14 (supports ≥3.13) | Latest stable; image uses `python:3.14.10-slim-trixie` |
| API | FastAPI 0.162.0, Uvicorn 0.54.0, Pydantic 2.14.2, pydantic-settings 2.16.2 | Typed, OpenAPI out of the box; settings from env |
| ORM / migrations | SQLAlchemy 2.1.8 (sync), Alembic 1.20.1 | Sync sessions keep RLS `SET LOCAL` simple; same code on SQLite and PostgreSQL |
| Database | PostgreSQL 17 (prod, RLS), SQLite (dev/trial/tests) | Tenant isolation in DB; zero-setup local runs |
| Jobs | Procrastinate 3.10.0 (PostgreSQL); in-process runner on SQLite | No Redis/broker to operate |
| HTTP out | httpx 0.28.1 | One client for payments, webhooks, IdPs; easy to fake |
| Clouds | boto3 1.43.124, kubernetes 37.0.1 | Official SDKs |
| AI | anthropic 2.5.0, default model `claude-opus-5-5` | Optional summaries, off unless key set |
| Crypto | PyJWT 2.15.1, cryptography 50.0.2 | OIDC, Fernet secrets at rest, Ed25519 licences |
| Other | psycopg 3.3.8, pyarrow 26.0.1, reportlab 5.1.0 | PG driver, Parquet CUR files, PDF reports |
| Quality | pytest 9.2.0, ruff 0.17.3, pyright 1.1.414 | |
| UI | React 19.3.1, TypeScript 7.0.2, Vite 8.3.5, Tailwind 4.3.4 | |
| UI kit | shadcn/ui pattern on radix-ui 1.8.1, lucide-react 1.68.0, ECharts 6.1.0, react-router 8.5.1, sonner 2.0.8, Geist (fontsource 5.3.0) | Accessible primitives, owned component code |
| UI quality | vitest 5.1.0, oxlint 1.91.0, `tsc --noEmit` | |
| Delivery | Docker multi-stage (node 26.13 → python 3.14.10), compose, Helm, k8s, Terraform | One image for every mode |
| CI/CD | GitHub Actions; GHCR multi-arch, SBOM, provenance, cosign keyless | |

### Direct dependencies and licences

All allow commercial closed-source use. Full transitive list: `THIRD_PARTY_NOTICES.md`
(generated, checked in CI by `tools/third_party_notices.py --check`).

| Package | Licence | Note |
|---|---|---|
| fastapi, pydantic, pydantic-settings, sqlalchemy, alembic, procrastinate, pyjwt | MIT | |
| uvicorn, httpx | BSD-3-Clause | |
| boto3, kubernetes, anthropic, pyarrow | Apache-2.0 | |
| cryptography | Apache-2.0 OR BSD-3-Clause | |
| reportlab | BSD | |
| psycopg, psycopg-binary, psycopg-pool | LGPL-3.0 | OK: used unmodified as a separate library; notice + licence text shipped |
| certifi (transitive) | MPL-2.0 | File-level copyleft; unmodified |
| react, react-dom, react-router, vite, tailwindcss, radix-ui, sonner, clsx, tailwind-merge, vitest, oxlint | MIT | |
| typescript, echarts, class-variance-authority | Apache-2.0 | |
| lucide-react | ISC | |
| Geist fonts | OFL-1.1 | Fonts bundled; OFL allows embedding |
| setuptools (build only) | MIT | |

## 3. Checklist

### 0. Foundation
- [x] 0.1 Skeleton: backend app factory, settings, DB layer, Alembic, job runners, health/ready/meta API, security headers, SPA serving; frontend shell (router, themes, shadcn components, empty states); tests; Dockerfile; compose (prod + local); CI + release workflows; LICENSE; generated third-party notices; Makefile
- [ ] 0.2 Structured JSON logging, request logging, error handler returning problem+json, Prometheus `/metrics`
- [ ] 0.3 Verify Docker image build + PostgreSQL test run locally (needs Docker running — see Q8)

### 1. Accounts, teams and security
- [ ] 1.1 Data model: users, organisations, memberships (viewer/member/admin/owner), sessions; tenant-scoped base model with `org_id`
- [ ] 1.2 PostgreSQL row-level security: per-request `SET LOCAL costlens.org_id`, policies on every tenant table, non-owner app role; tests proving cross-tenant reads fail
- [ ] 1.3 Application-level tenant guard (query scoping + tests) — works on SQLite too
- [ ] 1.4 Google sign-in (OIDC auth-code + PKCE, httpx, JWKS cache, PyJWT validation); fake IdP in tests
- [ ] 1.5 Generic OIDC provider config (Microsoft Entra, Okta, any OIDC) per instance and per org
- [ ] 1.6 Session cookies (signed, HttpOnly, SameSite=Lax, Secure), CSRF double-submit token, logout
- [ ] 1.7 Rate limiting (per IP / per user / per API key; PostgreSQL- or memory-backed)
- [ ] 1.8 Secrets at rest: Fernet with key rotation (`COSTLENS_ENCRYPTION_KEYS`), `EncryptedString` column type
- [ ] 1.9 Email invitations (token, expiry, accept flow); email sender abstraction (SMTP + fake) — BLOCKED(Q4) for SaaS provider only
- [ ] 1.10 Organisation switcher, roles management UI, member removal, ownership transfer
- [ ] 1.11 Audit log of every change (who, what, before/after, IP, request id) + UI with filters + export
- [ ] 1.12 Demo organisation with realistic generated data (multi-account AWS, K8s, anomalies, findings), read-only, resettable
- [ ] 1.13 Sign-in, onboarding and account UI pages

### 2. Cloud cost visibility
- [ ] 2.1 Canonical cost model (FOCUS-aligned line items, daily rollups, dimensions) + partitioning/indexing plan
- [ ] 2.2 AWS connection: CloudFormation/Terraform template for read-only role with external ID; STS assume-role check
- [ ] 2.3 Cost Explorer import (daily, grouped) with fake boto3 client
- [ ] 2.4 CUR 2.0 import from S3 (Parquet via pyarrow, streaming, idempotent per billing period)
- [ ] 2.5 CSV and FOCUS file import (upload + S3), column mapping, validation report
- [ ] 2.6 Daily/monthly spend API + charts; biggest movers; month-end forecast
- [ ] 2.7 Anomaly detection (seasonal baseline) + alerts
- [ ] 2.8 Breakdowns by account, service, region, tag, team, project; cost per resource; saved views
- [ ] 2.9 Change timeline: deployments/infra events (API, GitHub, CloudTrail) linked to cost changes
- [ ] 2.10 Data quality: missing/inconsistent tags, unallocated spend, tag normalisation rules
- [ ] 2.11 Multi-currency: daily FX rates (source BLOCKED(Q5)), display currency per user/org, INR/USD/EUR/GBP…

### 3. Kubernetes
- [ ] 3.1 Agent (Python, read-only RBAC) + ingest API with per-cluster tokens; Helm chart
- [ ] 3.2 Cost per cluster/namespace/workload; idle capacity
- [ ] 3.3 Requests vs usage (Prometheus or metrics-server); right-sizing never below observed peak (+ headroom)
- [ ] 3.4 Incident investigation: events, metrics, logs, recent changes → ranked causes with evidence, fixes, verification steps
- [ ] 3.5 Optional AI plain-English summary (anthropic SDK, fake in tests, redaction)
- [ ] 3.6 Detection of CrashLoopBackOff, OOMKilled, unhealthy workloads; Slack/Teams notifications

### 4. Optimisation
- [ ] 4.1 Findings framework: savings/month, risk, effort, steps/commands, dedup key, single ranked list without double counting
- [ ] 4.2 Waste detectors: unattached EBS, idle EIPs, unused LBs, old snapshots, stopped instances, previous-gen storage/instances, idle NAT
- [ ] 4.3 Right-sizing: EC2, RDS, containers from real usage
- [ ] 4.4 Database checks: oversized/idle, extended-support engine versions, Multi-AZ on non-prod
- [ ] 4.5 Savings Plans / RI coverage, utilisation, buy recommendations
- [ ] 4.6 Network/data-transfer: cross-AZ, internet egress, NAT processing
- [ ] 4.7 Workflow found→approved→applied→verified, owner, comments, history; realised savings measured from the bill
- [ ] 4.8 Reliability targets (SLOs) per service; recommendations respect them

### 5. Governance and reporting
- [ ] 5.1 Budgets + forecasts per team/project; alerts (email, Slack, Teams)
- [ ] 5.2 Policies (e.g. owner tag required) + violations
- [ ] 5.3 Allocation rules, showback/chargeback, shared-cost split rules
- [ ] 5.4 Monthly statements, invoice reconciliation, closed-month locking
- [ ] 5.5 Scheduled PDF (reportlab) and email reports
- [ ] 5.6 Unit economics: custom metrics ingest, cost per unit
- [ ] 5.7 Executive summary page

### 6. Integrations
- [ ] 6.1 Slack + Microsoft Teams notifications; weekly digest
- [ ] 6.2 Jira / ServiceNow tickets; GitHub / GitLab PRs for fixes
- [ ] 6.3 Cost impact estimate of planned changes (Terraform plan JSON, K8s manifests); CLI for CI
- [ ] 6.4 Outgoing webhooks: HMAC-signed, retries, public HTTPS only (SSRF guard: DNS resolve + private-range block)

### 7. More clouds and services
- [ ] 7.1 Azure cost + recommendations (read-only)
- [ ] 7.2 Google Cloud cost + recommendations (read-only)
- [ ] 7.3 AI/LLM costs incl. Bedrock; usage SDK for customers (Python + TypeScript)
- [ ] 7.4 Snowflake, Databricks, Datadog, MongoDB Atlas cost connectors

### 8. Enterprise and partners
- [ ] 8.1 Org API keys (read/write/admin scopes, hashed, rate-limited); public REST API docs (OpenAPI)
- [ ] 8.2 Teams inside an org; verified email domains (DNS TXT)
- [ ] 8.3 SCIM 2.0 user provisioning
- [ ] 8.4 Temporary elevated access with expiry + audit
- [ ] 8.5 Partner mode: manage client orgs, white-label branding, re-billing with markup
- [ ] 8.6 Opt-in automatic fixes: separate customer-approved role, approval steps, expiry, spend limits
- [ ] 8.7 Read-only MCP server for AI assistants (per-org API key)

### 9. Onboarding and usability
- [ ] 9.1 Guided setup wizard + next-steps checklist
- [ ] 9.2 Plain-English explanations + glossary for finance users
- [ ] 9.3 Responsive + accessibility audit (keyboard, contrast, screen reader), light/dark polish

### B. Business
- [ ] B.1 Plans, entitlements and limits engine (data-driven; prices BLOCKED(Q2))
- [ ] B.2 Subscriptions: trial, upgrade, downgrade, cancel — provider-agnostic core
- [ ] B.3 Razorpay (INR, GST invoices) — BLOCKED(Q2, Q3)
- [ ] B.4 Stripe (USD) — BLOCKED(Q2, Q3)
- [ ] B.5 Signed, de-duplicated payment webhooks (both providers)
- [ ] B.6 Self-hosted licence: Ed25519-signed offline key, grace period, free tier without licence
- [ ] B.7 Licence issuing tool (separate CLI; private key from file/env/KMS, never in repo or image)
- [x] B.8 Proprietary LICENSE + generated third-party notice (done in 0.1; legal review in Q6)
- [ ] B.9 Public website: product, pricing, getting started, security, support
- [ ] B.10 Draft legal pages: terms, EULA, privacy, DPA, SLA, refund — needs lawyer review (Q6)
- [ ] B.11 Docs: architecture, installation (SaaS + self-hosted), security, public API, releasing

### D. Delivery
- [x] D.1 Dockerfile (multi-stage), compose.yaml (web+worker+PG), deploy/compose/local.yaml
- [x] D.2 GitHub Actions CI (backend SQLite+PG, frontend, licences, image smoke test)
- [x] D.3 Release workflow: multi-arch GHCR, SBOM, provenance, cosign keyless
- [ ] D.4 Helm chart for in-cluster agent (with 3.1)
- [ ] D.5 Kubernetes manifests for the platform (self-hosted on K8s)
- [ ] D.6 Terraform for AWS hosting (VPC, ECS/EKS, RDS PG 17, secrets) — BLOCKED(Q7) for target design
- [ ] D.7 Pin GitHub Actions to commit SHAs (Dependabot/Renovate to keep fresh)

## 4. Design notes

**Layout**
```
backend/                  Python package `costlens` (src layout)
  src/costlens/
    config.py             Settings (COSTLENS_*), deployment modes, prod safety checks
    main.py / asgi.py     App factory / ASGI entry
    cli.py                `costlens migrate|serve|worker`
    api/                  Routers (system.py; one module per area) + deps.py
    core/                 Middleware, SPA serving, (later) security, crypto, rate limits
    db/                   Base, naming convention, mixins, Database/session
    models/               ORM models (all imported in models/__init__.py)
    jobs/                 Job registry + runners (in-process / Procrastinate)
    migrations/           Alembic env + versions (shipped in the wheel)
  tests/
frontend/                 React 19 + Vite + Tailwind 4 SPA (built into the image at /app/ui)
deploy/docker/            Container entrypoint
deploy/compose/           Local single-container compose
tools/                    Dev/release tools (third-party notices; later licence issuer)
compose.yaml              Production-style stack
docs/                     Documentation
```

**Decisions**
- *Sync SQLAlchemy.* FastAPI runs sync endpoints in a threadpool; sync sessions make
  per-transaction `SET LOCAL costlens.org_id` for RLS trivial and identical on SQLite.
- *Tenant isolation, two layers.* App layer: every tenant model has `org_id`; a
  session-level guard scopes queries to the current org. DB layer (PostgreSQL): RLS
  policies `org_id = current_setting('costlens.org_id')::uuid`; app connects as a
  non-owner role so RLS cannot be bypassed. Tests run the same suite on PG in CI.
- *Jobs.* `@job("name", cron=...)` registers plain functions in a backend-neutral
  registry. SQLite → `InProcessRunner` (runs inline, failures recorded not raised).
  PostgreSQL → `ProcrastinateRunner` registers tasks + periodic tasks; `costlens worker`
  runs the worker; `costlens migrate` applies the Procrastinate schema once.
- *One image, three modes.* `COSTLENS_DEPLOYMENT_MODE` = local|saas|self_hosted.
  Non-local modes refuse to start without `SECRET_KEY`, `ENCRYPTION_KEYS` and PostgreSQL.
  Billing (SaaS) and licence checks (self-hosted) will key off this setting.
- *UI served by the API process* at `/` with SPA fallback; `/api/*`, `/healthz`,
  `/readyz`, `/docs`, `/openapi.json` reserved. Strict CSP (`script-src 'self'`).
- *Container:* non-root uid 10001, `/data` volume (SQLite), entrypoint runs
  migrations before web (skip with `COSTLENS_SKIP_MIGRATE=1`).
- *IDs:* UUID4 primary keys; timestamps timezone-aware UTC; constraint naming convention
  for stable migrations.
- *Licence keys (planned):* Ed25519; payload JSON (customer, edition, limits, expiry,
  grace days) + signature, base64url. Public key embedded; private key only on the
  issuer's machine.
- *Third-party licences:* generator walks runtime dependency closures (Python
  metadata + node_modules), fails CI on strong copyleft/unknown or stale file.

## 5. Open questions for the owner

| # | Question | Blocks | Status |
|---|---|---|---|
| Q1 | **Product name** — decided: **CostLens** (2026-11-05). Remaining for you: register costlens.com (no DNS records on 2026-11-05, so likely unregistered; .io and .cloud are taken), trademark search on IP India (classes 9 & 42), USPTO and EUIPO — "lens" is a common product word, so check for similar marks in software/FinOps — and claim the GitHub org and social handles. | nothing (name in use) | decided; checks pending |
| Q2 | **Plans and prices**: plan names, INR and USD prices, trial length, limits (cloud spend under management, clusters, users, data retention), annual discount. | B.1, B.3, B.4, B.9 pricing page | open |
| Q3 | **Payment accounts**: Razorpay and Stripe accounts (business KYC as a sole proprietorship), GSTIN for GST invoices, place of supply rules, invoice numbering format. | B.3, B.4 | open |
| Q4 | **Transactional email provider** for SaaS (paid service): e.g. Amazon SES, Postmark, Resend — or SMTP only? Self-hosted will use customer SMTP. | 1.9 (SaaS sending) | open |
| Q5 | **Exchange-rate source**: free ECB/RBI reference rates (no cost, daily, limited currencies) vs a paid API. Recommend ECB + RBI reference rates. | 2.11 | open |
| Q6 | **Legal**: business address, contact emails (legal@, support@, security@), governing law/jurisdiction (suggest Tamil Nadu, India), and lawyer review of LICENSE, EULA, terms, privacy (DPDP Act 2023 + GDPR), DPA, SLA targets, refund policy. | B.10, final LICENSE | open |
| Q7 | **SaaS hosting design**: AWS region(s) (suggest ap-south-1 Mumbai + us-east-1), ECS Fargate vs EKS, budget. | D.6 | open |
| Q8 | Docker Desktop was not running in session 1, so the image build and PostgreSQL test run were not verified locally (CI does both). Start Docker if you want them verified next session. | 0.3 | open |
| Q9 | **GitHub org / image name**: workflows assume `ghcr.io/<repo owner>/costlens`; compose defaults to `ghcr.io/yugiverse/costlens`. Confirm the org name. | release | open |

## 6. Log

### 2026-11-05 — Session 1: skeleton
- Chose provisional name **Kanakku**, env prefix `KANAKKU_` (Q1).
- Looked up latest stable versions (PyPI, npm, Docker Hub, GitHub releases) and pinned exactly.
- Backend: settings with deployment modes and production safety checks; SQLAlchemy
  `Database` (SQLite pragmas, PG via psycopg); Alembic in-package with baseline
  migration (`instance_settings`); job registry + in-process and Procrastinate runners;
  `/healthz`, `/readyz`, `/api/v1/meta`; security headers + request IDs; SPA serving
  with path-traversal guard; `costlens` CLI (migrate/serve/worker).
- Frontend: Vite 8 + React 19 + TS 7 + Tailwind 4; theme provider (light/dark/system,
  persisted); shadcn-style Button/Card/DropdownMenu on radix-ui; responsive app shell
  with accessible mobile drawer (Radix Dialog) and skip link; overview + section
  placeholders + 404; typed API client.
- Delivery: multi-stage Dockerfile (non-root, healthcheck, entrypoint), compose.yaml
  (web + worker + PostgreSQL 17), local single-container compose, `.env.example`,
  CI (backend on SQLite and PostgreSQL, frontend, licence check, image smoke test),
  release (multi-arch GHCR, SBOM, provenance, cosign keyless, GitHub release).
- Proprietary LICENSE (draft, Q6); `tools/third_party_notices.py` → THIRD_PARTY_NOTICES.md.
- Results: backend 21 tests passed, ruff + pyright clean; frontend 12 tests passed,
  oxlint + tsc clean, production build OK; local smoke test of API + UI via
  `costlens serve` OK. **Not verified locally:** Docker image build and PostgreSQL test
  run (Docker daemon not running — Q8).
- Next: 0.2 (logging/errors/metrics), then 1.1.

### 2026-11-05 — Session 1b: rename to CostLens
- Owner chose **CostLens** (English name for a worldwide market) after a shortlist with DNS checks
  (costlens.com had no DNS records; .io/.cloud taken). Q1 now "decided; checks pending".
- Renamed everywhere: Python package `costlens` (`backend/src/costlens`), CLI `costlens`,
  env prefix `COSTLENS_`, image `ghcr.io/<owner>/costlens`, compose project/db names,
  UI name `costlens-web`, theme storage key `costlens-theme`, new lens favicon.
- Note: existing local SQLite files named `kanakku.db` are not migrated (none in use).
