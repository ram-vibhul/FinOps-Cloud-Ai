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
- [x] 0.2 Structured JSON logging, request logging, error handler returning problem+json, Prometheus `/metrics`
- [x] 0.3 Verify Docker image build + PostgreSQL test run locally (`make test-pg`; compose stack smoke-tested)
- [ ] 0.4 Operations polish: worker heartbeat + healthcheck (worker currently has none), slim the image (1.14 GB unpacked: check boto3/botocore data, pyarrow, uvicorn extras), compose smoke test in CI, migration-safety check (no destructive change without a flag)

### 1. Accounts, teams and security
- [x] 1.1 Data model: users, organisations, memberships (viewer/member/admin/owner), sessions; tenant-scoped base model with `org_id`
- [x] 1.2 PostgreSQL row-level security: per-request `SET LOCAL costlens.org_id`, policies on every tenant table, non-owner app role; tests proving cross-tenant reads fail
- [x] 1.3 Application-level tenant guard (query scoping + tests) — works on SQLite too
- [x] 1.4 Google sign-in (OIDC auth-code + PKCE, httpx, JWKS cache, PyJWT validation); fake IdP in tests
- [ ] 1.5 Generic OIDC provider config (Microsoft Entra, Okta, any OIDC) per instance and per org
- [~] 1.6 Session cookies (signed, HttpOnly, SameSite=Lax, Secure), CSRF double-submit token, logout — *cookie (`__Host-` prefix on HTTPS) + logout done in 1.4; CSRF protection still to do*
- [ ] 1.7 Rate limiting (per IP / per user / per API key; PostgreSQL- or memory-backed)
- [ ] 1.8 Secrets at rest: Fernet with key rotation (`COSTLENS_ENCRYPTION_KEYS`), `EncryptedString` column type
- [ ] 1.9 Email invitations (token, expiry, accept flow); email sender abstraction (SMTP + fake) — BLOCKED(Q4) for SaaS provider only
- [ ] 1.10 Organisation switcher, roles management UI, member removal, ownership transfer
- [ ] 1.11 Audit log of every change (who, what, before/after, IP, request id) + UI with filters + export
- [ ] 1.12 Demo organisation with realistic generated data (multi-account AWS, K8s, anomalies, findings), read-only, resettable
- [ ] 1.13 Sign-in, onboarding and account UI pages
- [ ] 1.14 Privacy rights (DPDP Act 2023, GDPR): self-service export of a user's and an organisation's data (JSON/CSV archive via background job, expiring download link); user account deletion and organisation deletion with grace period, cascade over all tenant tables + stored files, audit record kept without personal data
- [ ] 1.15 Data retention settings per organisation (raw cost lines, resource usage, K8s events/logs, audit log — within plan limits) + scheduled purge job; tests prove nothing older than the setting survives

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
- [ ] 2.12 (A) AWS permission self-check: test each read-only permission the connection needs (dry calls), show which are missing and exactly which checks/data they hide, with the policy snippet to add
- [ ] 2.13 (E) Resource inventory: every cost-driving resource with owner, age, state and cost; resource-level cost from CUR (resource IDs) with Cost Explorer fallback; hourly cost for the last 14 days
- [ ] 2.14 (D) Virtual tags: rule-based tags (match on account, service, resource, existing tags, names) for untagged or badly tagged costs; usable in every filter, breakdown, budget, allocation and report
- [ ] 2.15 (D) Anomalies explained in plain words (which service/account/usage type moved, by how much, likely driver, linked change), template-based with optional AI wording
- [ ] 2.16 (J) Consolidated view of AWS, Azure, Google Cloud and SaaS connectors together in one currency (after 7.1, 7.2, 7.4; grows as connectors land)
- [ ] 2.17 (J) Manual exchange-rate overrides per organisation (e.g. a fixed finance rate per month), taking precedence over daily rates and shown as overrides

### 3. Kubernetes
- [ ] 3.1 Agent (Python, read-only RBAC) + ingest API with per-cluster tokens; Helm chart
- [ ] 3.2 Cost per cluster/namespace/workload; idle capacity
- [ ] 3.3 Requests vs usage (Prometheus or metrics-server); right-sizing never below observed peak (+ headroom)
- [ ] 3.4 Incident investigation: events, metrics, logs, recent changes → ranked causes with evidence, fixes, verification steps
- [ ] 3.5 Optional AI plain-English summary (anthropic SDK, fake in tests, redaction)
- [ ] 3.6 Detection of CrashLoopBackOff, OOMKilled, unhealthy workloads; Slack/Teams notifications
- [ ] 3.7 (G) Agent-side redaction: remove secrets (tokens, keys, passwords, connection strings) and personal data (emails, phone numbers, IPs if configured) from logs and events inside the cluster before sending; tests with a corpus of real-looking secrets
- [ ] 3.8 (G) First-run health scan right after install (first useful answer in minutes) + on-demand read-only tasks from the UI (fetch logs, describe a workload) via an agent task queue with allow-list and audit
- [ ] 3.9 (G) Workload usage history from the agent alone (periodic metrics-server sampling, stored server-side) when Prometheus is missing
- [ ] 3.10 (G) Deployment history from GitHub Actions; metrics via Prometheus and OpenTelemetry (OTLP) in addition to metrics-server
- [ ] 3.11 (G) Cause-and-effect investigations: one investigation joining a cost spike, a deployment, an incident and a metric change on a shared timeline, instead of separate alerts
- [ ] 3.12 (G) "Did the fix work?": compare key signals before / during / after a fix or change, with a clear verdict
- [ ] 3.13 (G) AI summary also produces a ready-to-paste team status update (Slack/Teams/email-friendly)
- [ ] 3.14 (G) Test lab: scripted reproductions of real Kubernetes failures (OOM, crash loop, bad config, image pull, probe failure, node pressure, DNS) on a test cluster (kind), scoring how often the top-ranked diagnosis is right; tracked over time
- [ ] 3.15 (J) Start an investigation manually (cluster, namespace, workload, time window) and from incoming alert webhooks (Prometheus Alertmanager, PagerDuty, Opsgenie) — signed, de-duplicated by alert fingerprint
- [ ] 3.16 (J) Close an investigation with the real cause and what fixed it; feed this back to measure and improve top-ranked-cause accuracy (per check, over time, alongside the 3.14 lab score)
- [ ] 3.17 (J) Agent management: choose watched namespaces (system namespaces skipped by default), rotate/revoke a cluster's agent token, show agent version + last seen, warn when outdated or silent
- [ ] 3.18 (J) Agent auto-discovers in-cluster Prometheus; customers add their own metric queries (validated, read-only, rate-limited)
- [ ] 3.19 (J) Custom prices for clusters outside AWS (on-premises, other clouds): per vCPU, per GB memory, per node, Spot discount

### 4. Optimisation
- [ ] 4.1 Findings framework: savings/month, risk, effort, steps/commands, dedup key, single ranked list without double counting
- [ ] 4.2 Waste detectors: unattached EBS, idle EIPs, unused LBs, old snapshots, stopped instances, previous-gen storage/instances, idle NAT
- [ ] 4.3 Right-sizing: EC2, RDS, containers from real usage
- [ ] 4.4 Database checks: oversized/idle, extended-support engine versions, Multi-AZ on non-prod
- [ ] 4.5 Savings Plans / RI coverage, utilisation, buy recommendations
- [ ] 4.6 Network/data-transfer: cross-AZ, internet egress, NAT processing
- [ ] 4.7 Workflow found→approved→applied→verified, owner, comments, history; realised savings measured from the bill
- [ ] 4.8 Reliability targets (SLOs) per service; recommendations respect them
- [ ] 4.9 (A) AWS audit framework: a service-by-service checklist over the whole catalogue where every row ends as *checked & fine*, *saving found*, *needs more data* or *manual review*, with reasons; audit runs, history and a printable audit report
- [ ] 4.10 (A) Audit checks — compute: EC2, Auto Scaling, Spot, Lambda, ECS/Fargate, Batch; containers: ECR images and lifecycle policies, EKS (extended support, idle node groups)
- [ ] 4.11 (A) Audit checks — storage: S3 lifecycle, old object versions, incomplete multipart uploads, Intelligent-Tiering, Glacier, EBS, EFS, FSx, AWS Backup
- [ ] 4.12 (A) Audit checks — databases: RDS/Aurora per engine, DynamoDB capacity mode, ElastiCache, OpenSearch, Redshift, DocumentDB, Neptune, DMS
- [ ] 4.13 (A) Audit checks — analytics (Athena, Glue, EMR, QuickSight), AI/ML (SageMaker endpoints/notebooks, Bedrock provisioned throughput, knowledge bases), messaging (SQS, SNS, EventBridge, Step Functions, Kinesis, MSK, MQ)
- [ ] 4.14 (A) Audit checks — networking (public IPv4 charges, CloudFront, Route 53, Transit Gateway, VPN, Direct Connect), observability (CloudWatch log retention/ingestion, custom metrics, dashboards, alarms, X-Ray), security (KMS, Secrets Manager, WAF, CloudTrail), developer tools, end-user computing
- [ ] 4.15 (A) Non-production resources running 24×7 → schedule suggestions; Graviton and Spot suggestions
- [ ] 4.16 (A) Import AWS's own advice (Compute Optimizer, Cost Optimization Hub, Trusted Advisor) and merge with ours by resource + action, no duplicates, sources shown
- [ ] 4.17 (A) Purchase-advice pipeline decided in order: instance family/size → Graviton → Spot → Savings Plans / reservations, each step sized on the result of the previous one so recommendations never contradict
- [ ] 4.18 (B) Commitments inventory: every Savings Plan, reservation (EC2, RDS, ElastiCache, OpenSearch, Redshift) and negotiated contract (e.g. EDP/private pricing, entered manually) with expiry, utilisation and coverage; expiry alerts
- [ ] 4.19 (B) Laddered Savings Plan purchase plan + reservation planning for EC2, RDS, ElastiCache, OpenSearch, Redshift, sized conservatively (on a low percentile of steady usage, after 4.17)
- [ ] 4.20 (B) Purchase risk controls (spend limits, max term, upfront options), 7-day preview of planned purchases before anything is bought, advice for under-used reservations (exchange/sell/modify), savings verified after each purchase and month by month
- [ ] 4.21 (C) Network cost attribution from VPC Flow Logs (read-only, S3/CloudWatch): who causes NAT, cross-zone, inter-region and internet transfer cost — app, team, workload or IP — with monthly cost; joins K8s pod IPs
- [ ] 4.22 (F) Finding confidence (high / medium / low) with written reasons (data coverage, usage history length, volatility)
- [ ] 4.23 (F) "Doesn't apply to us" dismissals (with reason and scope, reversible) + per-check noise statistics
- [ ] 4.24 (F) Owner routing rules: which team or person owns which finding (by account, tag, virtual tag, service, namespace)
- [ ] 4.25 (F) Quick-wins plan: findings sorted into this week / this month / later from savings, effort, risk and confidence
- [ ] 4.26 (F) "What you kept": savings proven on the bill after a fix and rechecked monthly (detects regressions)
- [ ] 4.27 (F) ROI pack for finance: what the product saved vs its price, verified and estimated numbers kept separate, with the evidence behind each figure (PDF + CSV)
- [ ] 4.28 (G) Database and cache advice that improves performance as well as cost (connections, memory pressure, storage I/O, cache hit rate, engine features)
- [ ] 4.29 (G) Upgrade advice: old instance generations and extended-support charges (RDS, Aurora, EKS, ElastiCache, OpenSearch and others) with cost of staying vs upgrading

### 5. Governance and reporting
- [ ] 5.1 Budgets + forecasts; (D) nested budgets of any depth (organisation → department → team → project); alerts (email, Slack, Teams)
- [ ] 5.2 Policies (e.g. owner tag required) + violations
- [ ] 5.3 Allocation rules, showback/chargeback, shared-cost split rules
- [ ] 5.4 Monthly statements, invoice reconciliation, closed-month locking
- [ ] 5.5 Scheduled PDF (reportlab) and email reports
- [ ] 5.6 Unit economics: custom metrics ingest, cost per unit
- [ ] 5.7 Executive summary page
- [ ] 5.8 (D) Early warnings: forecast overspend before month end; same-day spend alerts (from hourly/CUR data where available)
- [ ] 5.9 (D) Custom cost report builder (dimensions, filters, grouping, period, chart type; saved and shared)
- [ ] 5.10 (D) "Ask in plain words": AI turns a sentence (e.g. "EC2 cost by region for 3 months") into a report-builder query — structured output validated against the schema, shown to the user before running, never raw SQL
- [ ] 5.11 (D) One-page monthly leadership report (PDF + email)
- [ ] 5.12 (D) FOCUS export; allocation that always adds up to the cent (largest-remainder rounding, invariant tests)

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
- [ ] 7.5 (H) Explain why AI spend changed (model, team, feature, tokens in/out, price changes); token-waste analyser: repeated prompts, oversized context, expensive model where a cheaper one would do, retries
- [ ] 7.6 (H) Bedrock spend per app and team from model invocation logs (read-only)
- [ ] 7.7 (H) SDK privacy: never sends prompt or completion text, only counts and fingerprints (salted hashes); enforced by tests
- [ ] 7.8 (J) Azure and Google Cloud setup help: exactly which read-only roles to grant (copyable commands), what the last collection saw (scopes, rows, date range, errors), and a "collect now" button

### 8. Enterprise and partners
- [ ] 8.1 Org API keys (read/write/admin scopes, hashed, rate-limited); public REST API docs (OpenAPI)
- [ ] 8.2 Teams inside an org; verified email domains (DNS TXT)
- [ ] 8.3 SCIM 2.0 user provisioning
- [ ] 8.4 Temporary elevated access with expiry + audit
- [ ] 8.5 Partner mode: manage client orgs, white-label branding, re-billing with markup
- [ ] 8.6 Opt-in automatic fixes: separate customer-approved role, approval steps, expiry, spend limits
- [ ] 8.7 Read-only MCP server for AI assistants (per-org API key)
- [ ] 8.8 (I) Fine-grained permissions beyond the four roles (custom roles from permission sets, scoped to teams/accounts)
- [ ] 8.9 (I) Enforce SSO for verified domains; people from a verified domain can join automatically (with default role)
- [ ] 8.10 (I) Partner portfolio sorted by what needs attention; client rebill statements as PDF in the partner's branding that never show the partner's cost or margin
- [ ] 8.11 (I) Auto clean-up: find idle resources and file approval requests — never act without approval and every guard satisfied; approved reservation changes through the separate opt-in role (8.6)
- [ ] 8.12 (J) Write-access banner: when automatic fixes (8.6) are enabled for an organisation, a clear banner on every page with who enabled it and when, plus one-click "turn off" (audited)
- [ ] 8.13 (J) Partner sign-up: self-serve or approved by Yugiverse (instance setting, default: approval required); client invitation links that expire; logo upload size limit and format check (PNG/SVG/WebP, SVG sanitised)
- [ ] 8.14 (J) MCP connector may also create a new investigation — the only write it allows (scoped key permission, audited)

### 9. Onboarding and usability
- [ ] 9.1 Guided setup wizard + next-steps checklist
- [ ] 9.2 Plain-English explanations + glossary for finance users
- [ ] 9.3 Responsive + accessibility audit (keyboard, contrast, screen reader), light/dark polish
- [ ] 9.4 In-app notification centre: per-user inbox (alerts, finished/failed imports, approvals waiting, invitations), unread badge, mark read, per-type preferences; one notification service that also fans out to email/Slack/Teams
- [ ] 9.5 (I) Onboarding that shows the first savings quickly, a trial countdown, and a live check that the billing export bucket is readable

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
- [ ] B.12 Usage metering per organisation (spend under management, connected accounts, clusters, users, API calls, AI summaries): daily snapshots, feeds plan-limit checks (B.1) and billing (B.2–B.4); visible to the customer
- [ ] B.13 Public status page and changelog / release notes page (changelog generated from release notes in the repo; status from uptime checks in D.9)
- [ ] B.14 (J) Licence status in the UI for self-hosted: plan, limits and usage, days left, warnings before expiry and during the grace period
- [ ] B.15 (J) Monthly and annual plans; proration on plan changes; downloadable invoices and receipts (GST invoices for India) — BLOCKED(Q2, Q3)
- [ ] B.16 (J) Failed payments: retries, reminder emails, grace period, then downgrade to the free tier without deleting data
- [ ] B.17 (J) RBI e-mandate rules for Indian card/UPI auto-renewal: pre-debit notification, customer approval for charges above the limit — BLOCKED(Q3)

### D. Delivery
- [x] D.1 Dockerfile (multi-stage), compose.yaml (web+worker+PG), deploy/compose/local.yaml
- [x] D.2 GitHub Actions CI (backend SQLite+PG, frontend, licences, image smoke test)
- [x] D.3 Release workflow: multi-arch GHCR, SBOM, provenance, cosign keyless
- [ ] D.4 Helm chart for in-cluster agent (with 3.1)
- [ ] D.5 Kubernetes manifests for the platform (self-hosted on K8s)
- [ ] D.6 Terraform for AWS hosting (VPC, ECS/EKS, RDS PG 17, secrets) — BLOCKED(Q7) for target design
- [ ] D.7 Pin GitHub Actions to commit SHAs (Dependabot/Renovate to keep fresh)
- [ ] D.8 Backup and restore for self-hosted: `costlens backup` / `costlens restore` commands (pg_dump/pg_restore for PostgreSQL, online copy for SQLite, includes encryption-key reminder), guide covering schedule, off-site copies and a tested restore drill
- [ ] D.9 SaaS error tracking and uptime monitoring with self-hosted open-source tools, no paid service by default: Sentry-protocol SDK (MIT) reporting to a self-hosted GlitchTip (MIT), Uptime Kuma (MIT) for uptime checks + alerting; error reporting off unless `COSTLENS_ERROR_TRACKING_DSN` is set; PII scrubbing. Hosting cost for these → ask (Q7)

## 4. Design notes

- *Features A–I (added 2026-11-05).* Integrated into sections 2–9 and tagged with
  their letter. Key shared designs: **audit = findings + coverage**: each audit row
  is a check result (fine / saving / needs data / manual review) and every "saving"
  row is a finding in the single ranked list, so the audit and the opportunity list
  never disagree. **Purchase pipeline** (4.17) runs in a fixed order and each stage
  consumes the previous stage's *post-change* usage, which is what prevents
  contradicting advice and double counting. **Verified vs estimated** savings are
  separate fields everywhere (findings, "what you kept", ROI pack). **Purchases are
  never automatic** without the opt-in role, limits and the 7-day preview.


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

- *New items (added 2026-11-05).* Retention purge and deletion reuse one per-table
  "tenant data map" so every tenant table is covered and a test fails when a new
  tenant table is missing from it. The notification centre is the single fan-out point
  for alerts (in-app, email, Slack, Teams). Usage metering writes daily per-org
  snapshots; plan limits read them, billing reports them. Monitoring stays open-source
  and self-hosted (GlitchTip, Uptime Kuma); the status page can be Uptime Kuma's built-in
  page at first.

- *Observability (0.2).* `core/logging.py`: one stdout handler; JSON lines (`ts, level,
  logger, msg, request_id, …extra`) unless local mode (`COSTLENS_LOG_FORMAT=auto|json|text`).
  `RequestContextMiddleware` sets the request id (context variable), writes one access
  log line per request with the *route template* (not raw path, so no IDs/PII in metric
  labels), records metrics, catches unhandled errors (logged with stack, returned as a
  generic 500 problem with the request id) and adds security headers to every response.
  Errors are RFC 9457 problem+json with a stable `code`; `AppError` subclasses
  (`NotFoundError`, `PermissionDeniedError`, `ConflictError`) are what services raise.
  `/metrics` uses a tiny built-in Prometheus registry (no new dependency); it needs
  `COSTLENS_METRICS_TOKEN` as a bearer token, and is hidden (404) outside local mode
  when no token is set. Metrics are per process.
- *Accounts (1.1).* Global `users` and `identities` (provider + subject; a new provider
  links to an existing user by email **only when the IdP says the email is verified**).
  `organizations` (slug, demo flag, `deletion_requested_at` for the 1.14 grace period).
  `memberships` is the first tenant table. `sessions` store only a SHA-256 of a random
  token, an active organisation, expiry and last-seen (touched at most every 5 min).
  `TenantMixin` adds `org_id` (FK, cascade) and registers the table in
  `db.tenant.tenant_tables()` — the list RLS, the app guard, export/deletion and
  retention will all use. Rules in `accounts/service.py`: admins manage
  viewers/members/admins, only owners touch owners, an organisation always keeps one
  owner, outsiders get "not found" rather than "forbidden".

- *Row-level security (1.2).* Policy `tenant_isolation` on every tenant table, with
  `ENABLE` + `FORCE` (so even the table owner obeys). A row passes when
  `org_id = current_setting('costlens.org_id')` or `costlens.bypass_rls = 'on'`;
  tables with a user column also let that user *read* their own rows across
  organisations (needed for the organisation switcher). No setting means no rows.
  `set_tenant(session, org_id=…, user_id=…, bypass=…)` stores the context on the
  SQLAlchemy session; an `after_begin` listener applies it with `set_config(…, true)`
  at the start of every transaction, so it survives commits and never leaks to a
  pooled connection. Two database roles: owner (migrations only, via
  `COSTLENS_MIGRATION_DATABASE_URL`) and the runtime role (`COSTLENS_DATABASE_URL`,
  no superuser/BYPASSRLS, data privileges only, granted by `costlens migrate`). In
  SaaS/self-hosted mode the app **refuses to start** on a privileged role unless
  `COSTLENS_ALLOW_PRIVILEGED_DB_ROLE=true`. Compose runs a one-shot `migrate` service
  holding the owner password; web and worker only get the runtime role. A test fails
  if any tenant table lacks enforced RLS, and the PostgreSQL test run uses the
  runtime role, so every test exercises RLS. New tenant tables: call
  `enable_tenant_rls_sql()` in their migration. Docs (B.11) must explain creating the
  runtime role for customers using their own PostgreSQL.
- *Jobs on PostgreSQL.* Procrastinate uses its async psycopg connector (the worker
  requires it); web requests still defer jobs synchronously through `app.open()`.

- *Application tenant guard (1.3).* SQLAlchemy `do_orm_execute` adds
  `with_loader_criteria(org_id = current org)` to every ORM SELECT/UPDATE/DELETE that
  touches a tenant model (joins and aliases included; plus "own rows" for models that
  declare `__tenant_user_column__`, mirroring RLS). Querying a tenant model with no
  organisation context **raises** `TenantIsolationError` instead of returning an empty
  or global result, so a missing `set_tenant()` fails loudly in tests. `before_flush`
  fills a missing `org_id` from the context and rejects creating rows in another
  organisation, moving rows between organisations, and changing/deleting rows of an
  organisation other than the current one. `bypass=True` (system jobs) or the
  `costlens_skip_tenant_guard` execution option turns the guard off; on PostgreSQL
  RLS still applies underneath (tested). Raw `text()` SQL is only covered by RLS.

- *Sign-in (1.4).* Generic OpenID Connect relying party (`auth/oidc.py`) with Google as
  the first configured provider (`auth/providers.py`; 1.5 adds Entra/Okta/any OIDC as
  more `ProviderConfig`s). Flow: `GET /api/v1/auth/login/{provider}` creates state,
  nonce and a PKCE (S256) pair, stores them in a short-lived (10 min) HMAC-signed,
  HttpOnly, SameSite=Lax cookie scoped to `/api/v1/auth`, and redirects (303) to the
  IdP. `GET /callback/{provider}` checks the cookie and state, exchanges the code
  (client secret + PKCE verifier) and verifies the ID token: asymmetric algorithms only
  (no `none`/HS*), JWKS key by `kid` with a rate-limited refresh for key rotation,
  issuer, audience, `azp`, expiry (60 s leeway) and nonce. Then `sign_in_with_identity`
  (links by email only if `email_verified`), optional `COSTLENS_ALLOWED_EMAIL_DOMAINS`,
  and a session cookie (`__Host-costlens_session` on HTTPS, HttpOnly, SameSite=Lax,
  14 days; the DB keeps only a hash). Failures redirect to `/sign-in?error=<code>`
  (no details leaked; logged server-side). `return_to` accepts same-site paths only.
  `GET /me` returns the user, organisations, active organisation and role;
  `POST /logout` revokes the session. Every request with a session cookie sets the
  tenant context (active org + user). Outbound HTTP: one shared `httpx.Client` on
  `app.state.http` (tests inject `httpx.MockTransport`); httpx URL logging silenced.

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
| Q8 | ~~Docker Desktop was not running in sessions 1 and 2, so the image build and PostgreSQL test run were not verified locally (CI does both). Start Docker if you want them verified next session. **Now also needed for 1.2 (row-level security), which can only be tested on PostgreSQL** — without Docker I can build it but only CI will prove it.~~ Resolved 2026-11-05: Docker running; image, compose and PostgreSQL tests verified. | — | resolved |
| Q10 | **Google OAuth client for the SaaS** (free): create a Google Cloud project for Yugiverse, configure the OAuth consent screen (app name CostLens, logo, privacy/terms links, support email; Google verification needed for external users) and an OAuth client (Web) with redirect `https://<saas domain>/api/v1/auth/callback/google`. Give me the client ID; the secret goes only into the hosting secrets. Self-hosted customers create their own. | SaaS sign-in go-live | open |
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

### 2026-11-05 — Checklist additions
- Added at owner's request: 1.14 data export + account/organisation deletion (DPDP Act
  2023, GDPR); 1.15 per-org data retention + automatic purge; 9.4 in-app notification
  centre; B.12 usage metering per organisation; B.13 public status page + changelog;
  D.8 self-hosted backup/restore commands and guide; D.9 SaaS error tracking + uptime
  monitoring with self-hosted open-source tools (no paid service by default).
- Design note added for these. No code changes.

### 2026-11-05 — Checklist: features A–I
- Added owner's features A–I into the right sections (tagged (A)…(I)): 2.12–2.15,
  3.7–3.14, 4.9–4.29, 5.1 (nested budgets), 5.8–5.12, 7.5–7.7, 8.8–8.11, 9.5.

### 2026-11-05 — Session 2: 0.2 observability, 1.1 accounts model
- **0.2 done:** JSON/text logging with request ids; access log with route templates;
  problem+json errors for 404/422/AppError/unhandled 500 (no internal details leaked,
  security headers still applied); `/metrics` (built-in Prometheus text registry, token
  protected; HTTP counts + latency histogram, job outcomes, build info). Frontend API
  client now reads problem+json (`code`, `requestId`).
- **1.1 done:** models `User`, `Identity`, `Organization`, `Membership` (tenant),
  `UserSession`; `TenantMixin` + tenant table registry; role/permission rules; account
  service (sign-in with identity, organisations, members, ownership transfer, last-owner
  protection, sessions create/resolve/switch/revoke); migration `0002_accounts`.
- **0.3 still open:** Docker daemon not running and no local PostgreSQL server (Q8).
- Results: backend 65 tests passed, ruff + pyright clean; frontend 13 tests passed,
  oxlint + tsc clean, build OK; licence notices up to date. Tests ran on SQLite only.
- Next: 1.2 PostgreSQL row-level security (best with Docker running — Q8), then 1.3.

### 2026-11-05 — Session 3: 0.3 verified, 1.2 row-level security
- **0.3 done (Docker now running):** full suite passes on PostgreSQL 17.11; image builds
  (1.14 GB unpacked; slimming → 0.4), runs as uid 10001, healthcheck healthy, serves API
  + UI; compose stack verified end to end. Added `make test-pg` / `make pg-stop`.
- **Bug found and fixed by the compose test:** the job worker crashed at start
  (Procrastinate's worker needs the async connector). Switched to `PsycopgConnector`
  and an asyncio worker; added a PostgreSQL test that defers a job and runs the worker.
  Worker now logs through our logging setup; uvicorn's `color_message` dropped from JSON logs.
- **1.2 done:** RLS helper functions + policy on `memberships` (migration 0003), tenant
  context per transaction, runtime/owner role split, grants in `costlens migrate`,
  startup refusal on privileged roles, compose `migrate` service + `costlens_app` role
  created by an init script. 15 PostgreSQL-only tests: no context → no rows; org scoping;
  user reads own memberships across orgs; cross-org insert/move/update/delete blocked;
  services can't reach other orgs; bypass; context survives commit and doesn't leak to
  pooled connections; app role can't disable RLS or edit `alembic_version`; startup
  guard; coverage guard over all tenant tables.
- Results: SQLite 67 passed / 15 skipped (PG-only); **PostgreSQL 82 passed** (as the
  non-privileged app role); ruff + pyright clean; frontend 13 passed, oxlint + tsc
  clean, build OK; licence notices current.
- Next: 1.3 application-level tenant guard (SQLite + defence in depth), then 1.4 Google sign-in.

### 2026-11-05 — Checklist additions (J)
- Added owner's batch (tagged (J)): 2.16–2.17, 3.15–3.19, 7.8, 8.12–8.14, B.14–B.17.
  Partner sign-up mode chosen as an instance setting defaulting to "approval required".

### 2026-11-05 — Session 4: 1.3 application tenant guard
- **1.3 done:** automatic org scoping of ORM queries, loud failure without context,
  write checks on flush (fill/forbid cross-org create, move, change, delete), bypass
  and per-query skip; `Membership.__tenant_user_column__`. 14 new tests run on both
  SQLite and PostgreSQL; RLS tests now use raw SQL so they test the database layer
  independently. Proved defence in depth: skipping the app guard on PostgreSQL still
  returns no rows thanks to RLS.
- Results: SQLite 81 passed / 15 skipped (PG-only); PostgreSQL 96 passed; ruff + pyright
  clean; frontend 13 passed, oxlint + tsc clean, build OK; licence notices current.
- Next: 1.4 Google sign-in (OIDC + PKCE) with a fake identity provider in tests.

### 2026-11-05 — Session 5: 1.4 Google sign-in
- **1.4 done:** OIDC client (discovery + JWKS caching, key rotation, strict ID-token
  checks), PKCE + state + nonce in a signed cookie, callback creating user/identity and
  session cookie, `/auth/providers`, `/auth/me`, `/auth/logout`, request authentication
  dependency setting the tenant context, allowed email domains, `Signer` utility.
  Also set httpx logging to WARNING (it logged full URLs).
- **1.6 partly done:** session cookie and logout; CSRF protection remains.
- Tests: in-memory fake Google (`tests/fakes/oidc.py`) — 36 new tests: full flow, return
  path, cookie flags, `__Host-` on HTTPS, logout + reuse of a stolen cookie, forged state,
  tampered/missing state cookie, code replay, IdP error, PKCE mismatch, 8 bad-token cases
  (nonce, audience, issuer, expired, HS256, foreign key, azp, garbage) + unsigned token,
  key rotation, unverified-email takeover, string `email_verified`, missing email, domain
  allow-list, IdP down, unknown provider, open-redirect cases, signer.
- New question Q10 (Google OAuth client for the SaaS).
- Results: SQLite 117 passed / 15 skipped; PostgreSQL 132 passed; ruff + pyright clean;
  frontend 13 passed, oxlint + tsc clean, build OK; licence notices current.
- Next: 1.5 other OIDC providers (Entra, Okta, generic) per instance and per organisation.
