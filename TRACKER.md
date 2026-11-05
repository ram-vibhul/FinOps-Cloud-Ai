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
- [x] 1.5 Generic OIDC provider config (Microsoft Entra, Okta, any OIDC) per instance and per org
- [x] 1.6 Session cookies (signed, HttpOnly, SameSite=Lax, Secure), CSRF double-submit token, logout
- [x] 1.7 Rate limiting (per IP / per user / per API key; PostgreSQL- or memory-backed)
- [x] 1.8 Secrets at rest: Fernet with key rotation (`COSTLENS_ENCRYPTION_KEYS`), `EncryptedString` column type
- [x] 1.9 Email invitations (token, expiry, accept flow); email sender abstraction (SMTP + fake) — SMTP works with any provider; SaaS provider choice still Q4
- [x] 1.10 Organisation switcher, roles management UI, member removal, ownership transfer
- [x] 1.11 Audit log of every change (who, what, before/after, IP, request id) + UI with filters + export
- [~] 1.12 Demo organisation with realistic generated data (multi-account AWS, K8s, anomalies, findings), read-only, resettable — *framework done (join, read-only, reset, deterministic seeders); every feature from section 2 on must register a `@demo_seeder` for its data*
- [x] 1.13 Sign-in, onboarding and account UI pages
- [x] 1.14 Privacy rights (DPDP Act 2023, GDPR): self-service export of a user's and an organisation's data (JSON/CSV archive via background job, expiring download link); user account deletion and organisation deletion with grace period, cascade over all tenant tables + stored files, audit record kept without personal data
- [x] 1.15 Data retention settings per organisation (raw cost lines, resource usage, K8s events/logs, audit log — within plan limits) + scheduled purge job; tests prove nothing older than the setting survives

### 2. Cloud cost visibility
- [x] 2.1 Canonical cost model (FOCUS-aligned line items, daily rollups, dimensions) + partitioning/indexing plan
- [x] 2.2 AWS connection: CloudFormation/Terraform template for read-only role with external ID; STS assume-role check
- [x] 2.3 Cost Explorer import (daily, grouped) with fake boto3 client
- [x] 2.4 CUR 2.0 import from S3 (Parquet via pyarrow, streaming, idempotent per billing period)
- [x] 2.5 CSV and FOCUS file import (upload + S3), column mapping, validation report
- [x] 2.6 Daily/monthly spend API + charts; biggest movers; month-end forecast
- [x] 2.7 Anomaly detection (seasonal baseline) + alerts — *email alerts now; Slack/Teams with 6.1, in-app with 9.4*
- [x] 2.8 Breakdowns by account, service, region, tag, team, project; cost per resource; saved views
- [x] 2.9 Change timeline: deployments/infra events (API, GitHub, CloudTrail) linked to cost changes
- [x] 2.10 Data quality: missing/inconsistent tags, unallocated spend, tag normalisation rules
- [x] 2.11 Multi-currency: daily FX rates, display currency per user/org, INR/USD/EUR/GBP… — *built with ECB reference rates as the default provider; confirm or change in Q5*
- [x] 2.12 (A) AWS permission self-check: test each read-only permission the connection needs (dry calls), show which are missing and exactly which checks/data they hide, with the policy snippet to add
- [x] 2.13 (E) Resource inventory: every cost-driving resource with owner, age, state and cost; resource-level cost from CUR (resource IDs) with Cost Explorer fallback; hourly cost for the last 14 days
- [x] 2.14 (D) Virtual tags: rule-based tags (match on account, service, resource, existing tags, names) for untagged or badly tagged costs; usable in every filter, breakdown, budget, allocation and report
- [x] 2.15 (D) Anomalies explained in plain words (which service/account/usage type moved, by how much, likely driver, linked change), template-based with optional AI wording
- [ ] 2.16 (J) Consolidated view of AWS, Azure, Google Cloud and SaaS connectors together in one currency (after 7.1, 7.2, 7.4; grows as connectors land)
- [x] 2.17 (J) Manual exchange-rate overrides per organisation (e.g. a fixed finance rate per month), taking precedence over daily rates and shown as overrides

### 3. Kubernetes
- [x] 3.1 Agent (Python, read-only RBAC) + ingest API with per-cluster tokens; Helm chart
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
- [x] D.4 Helm chart for in-cluster agent (with 3.1)
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

- *Secrets at rest (1.8).* `core/crypto.py`: `SecretBox` over `MultiFernet` from
  `COSTLENS_ENCRYPTION_KEYS` (comma-separated, first key encrypts, all decrypt);
  ciphertext prefixed `v1:`. `EncryptedString` column type encrypts on write and
  decrypts on read. Rotation: prepend a new key, run `costlens rotate-keys` (re-encrypts
  every `EncryptedString` column via raw SQL with RLS bypass, idempotent), then drop the
  old key. Local mode without keys generates `<data_dir>/.encryption-key` (0600).
  Not yet: per-row associated data (a ciphertext copied to another row would still
  decrypt) — acceptable while only admins write these columns.
- *Identity providers (1.5).* Instance-wide providers from `COSTLENS_OIDC_PROVIDERS`
  (JSON: `entra` by tenant, `okta` by domain or issuer, `oidc` by issuer) plus Google.
  Per-organisation SSO in `org_identity_providers` (tenant table, RLS, client secret
  encrypted), managed by admins at `/api/v1/org/sso` (list/create/patch/delete/test;
  secrets write-only), public list per org slug at
  `/api/v1/auth/organizations/{slug}/providers`, login id `org:<uuid>`.
  Multi-tenant Entra (`organizations`/`common`): issuer verified per token against its
  `tid`; identity subject is `tid:oid` (Entra `sub` is per app). `trust_email` (off by
  default) treats an IdP's email as verified, required to link to an existing account
  (protects against the Entra "nOAuth" email-claim takeover). Customer-supplied issuer,
  token and JWKS URLs pass an SSRF guard (`core/netguard.py`: https only, port 443, no
  credentials, every resolved address public); DNS rebinding is closed in 6.4.
- *Test helpers.* `tests/helpers.py` creates users/orgs/sessions directly;
  `tests/fakes/oidc.py` poses as any issuer; `public_resolver` fakes DNS
  (`*.internal` resolves to a private address).

- *CSRF (1.6).* `core/csrf.py` ASGI middleware. Token = HMAC(signing key, session
  token): session-bound and stateless. Set at sign-in as a readable `costlens_csrf`
  cookie and returned by `/me`; the web client adds `X-CSRF-Token` to unsafe requests.
  For POST/PUT/PATCH/DELETE that carry the session cookie the middleware requires the
  token **and** rejects `Sec-Fetch-Site: cross-site`, `Origin: null` and any `Origin`
  other than `COSTLENS_BASE_URL`/CORS origins. Requests without the session cookie
  (API keys, webhooks, SCIM) are left to their own authentication. Session cookies are
  also SameSite=Lax as a further layer.

- *Rate limiting (1.7).* `core/ratelimit.py` ASGI middleware on `/api/*` (health,
  readiness, metrics and static assets exempt). Fixed one-minute windows; rules:
  sign-in endpoints per IP (20), anonymous per IP (120), signed-in per session (600),
  writes per session (120) — all `COSTLENS_RATE_LIMIT_*` settings. Session identity is
  a hash of the cookie (no DB lookup). Backends: memory (SQLite) or the shared
  `rate_limit_buckets` table (PostgreSQL; one atomic upsert per counted request, so all
  workers/containers share limits), cleaned by the periodic job
  `system.cleanup_rate_limits`. 429 problem+json with `Retry-After`; successful API
  responses carry `RateLimit-Limit/Remaining/Reset`. Metric
  `costlens_rate_limited_total{rule}`. API-key identity is added with 8.1. Client IP
  relies on Uvicorn `--forwarded-allow-ips` being set to the real proxy.

- *Email + invitations (1.9).* `core/email.py`: `Email`, `SmtpSender` (STARTTLS/SSL,
  any provider), `ConsoleSender` (no SMTP host: logged), `MemorySender` (tests). Mail
  is sent by the `email.send` job (5 retries on PostgreSQL). Jobs now support `retry`.
  `invitations` (tenant table, RLS): email, role, token hash, inviter, 7-day expiry,
  accepted/revoked. A new invitation to the same email revokes pending ones. Accepting
  requires a signed-in user **whose email equals the invited email** (forwarded links
  are useless), adds the membership and switches the active organisation. API:
  `POST/GET/DELETE /api/v1/org/invitations` (admins; owners for owner invites; the
  response includes the link once so it can be shared without email),
  `GET /api/v1/invitations/{token}` (public preview, masked email),
  `POST /api/v1/invitations/{token}/accept`. HTML email escapes all names.

- *Audit log (1.11).* `audit_events` (tenant table, RLS; app role has **no UPDATE**
  privilege, so history cannot be rewritten; deletes only through retention/org
  deletion). Models opt in with `__audited__ = True`; `__audit_redact__` columns are
  recorded as `[redacted]`. A `before_flush` hook writes `<model>.created/updated/
  deleted` with field-level `[before, after]` in the same transaction (no-op updates
  are skipped). Non-row events use `audit.recorder.record()` (e.g. organisation
  created, SSO provider tested). Actor (user id + email snapshot, IP, user agent) is set
  by request authentication; the request id comes from the logging context; jobs act
  as `system`. API (admins): `GET /api/v1/org/audit-events` with action prefix, actor,
  target, time filters and keyset cursor pagination; `GET …/export` streams CSV (max
  100k rows) with spreadsheet formula injection neutralised. Sign-in/sign-out are not
  organisation events; they go to the per-user security log planned with 1.14.
- *No network in tests.* An autouse fixture makes every real httpx transport fail; apps
  in tests get an offline client unless a fake (IdP, etc.) is injected.

- *Demo organisation (1.12).* Slug `demo` (reserved, along with other product slugs, so
  customers can never take it). `POST /api/v1/demo/join` adds the user as a viewer and
  switches to it; `require()` refuses every non-read permission in a demo org
  (`demo_read_only`), even for admins; leaving still works. Features register
  `@demo_seeder(name, order)` functions that get a `DemoContext` (org id, today, a
  random generator seeded per seeder name), so data is identical for everyone and
  always ends today. `reset_demo` deletes every tenant table's demo rows except
  memberships and reseeds; nightly job `demo.reset` (03:17) and `costlens demo
  seed|reset`. `COSTLENS_DEMO_ENABLED` (default true).

- *Web app screens (1.13).* `SessionProvider` loads `/me` (states: loading, anonymous,
  error with retry, ready) and feeds the CSRF token to the API client.
  `RequireSession` sends anonymous visitors to `/sign-in?return_to=…` and people
  without an organisation to `/welcome`. Pages: sign-in (instance providers, company
  SSO lookup by organisation, plain-English error messages, local dev sign-in),
  invitation (preview → sign in → accept; wrong-account message), welcome (create
  organisation or explore the demo), header organisation switcher + account menu,
  demo banner, settings tabs: General, Members (roles, remove/leave, make owner,
  invite dialog that shows the link, pending invitations), Single sign-on (add
  Entra/Okta/OIDC, test, enable/disable, delete), Audit log (filters, load more, CSV).
  Admin-only tabs are hidden from others; the server enforces it anyway.
  UI primitives added: Input, Label, NativeSelect, Badge, Dialog, Tabs, Table.
  Frontend tests use `mockApi()` (fake API by "METHOD /path").
- *Local development sign-in.* `COSTLENS_LOCAL_LOGIN=true` (refused outside local mode)
  adds an email-only sign-in for trials and development; `make dev-api` and the local
  compose file enable it, and local compose binds to 127.0.0.1 only. In local mode the
  CSRF check accepts any localhost origin (Vite dev server port), and the signing key
  is kept in `<data_dir>/.signing-key` so restarts do not break sessions.

- *Privacy rights (1.14).* `privacy/service.py`. Personal export (`GET /api/v1/me/export`,
  JSON: profile, sign-in methods, organisations, sessions, actions). Account deletion
  (`DELETE /api/v1/me`, type your email): refused while sole owner of an organisation;
  records `member.account_deleted` in each organisation, deletes the user (identities,
  memberships, sessions cascade), then pseudonymises their audit trail (`deleted user`,
  no IP/user agent) — the app role has column-level UPDATE on exactly those audit
  columns. Organisation export (owners): `DataExport` rows (tenant table), ZIP built by
  job `privacy.build_export` — one JSON file per tenant table + organisation + manifest;
  `EncryptedString` columns and `token_hash` excluded; download for 7 days, then
  `privacy.cleanup_exports` deletes it. Organisation deletion (owners, type the slug):
  hidden at once (members lose access on their next request), restorable by owners for
  30 days (`POST /api/v1/organizations/{id}/restore`), then `privacy.purge_deleted_
  organizations` deletes the organisation row and its exports; foreign keys cascade to
  every tenant table (test checks all of them). People keep their accounts.
  Files: `core/storage.py` (`LocalStorage` under `<data_dir>/files`, safe keys only;
  S3 backend with D.6). Jobs get the database/settings/storage from a process-wide
  `JobContext` (web process: the app's own database; worker: from settings).

- *Retention (1.15).* `privacy/retention.py`: features register a `RetentionPolicy`
  (kind, label, table, time column, default/min/max days). First policy: audit log
  (365 days default, 90–2555). Organisations store choices in
  `organizations.retention_days` (JSON; stored values outside bounds are clamped);
  `GET/PATCH /api/v1/org/retention` (read: anyone; change: admins; changes are audited).
  Nightly job `privacy.apply_retention` (03:10) deletes older rows per organisation.
  **Every new time-series table must register a policy** (cost lines, usage samples,
  Kubernetes events/logs, notifications…). Plan limits (B.1) will narrow the bounds.

- *Cost model (2.1).* `models/costs.py`: `cloud_accounts` (provider, external id,
  payer/linked hierarchy, environment, tags; audited), `cost_import_runs`,
  `cost_line_items` (daily FOCUS-aligned facts: provider, billing/sub account, service
  and category, region/zone, resource id/name/type, SKU, usage type, operation, charge
  and pricing category, commitment discount, quantity/unit, billed/effective/list cost,
  currency, tags JSON, provider extras JSON), `cost_daily` (rollup by day, provider,
  account, service, region, charge/pricing category, currency). Money `Numeric(24,10)`.
  `costs/ingest.py`: `CostLine` (validated) and `replace_billing_period()` — deletes the
  (source, billing period) slice, bulk-inserts in batches of 5,000 with Core, then
  `refresh_daily()` rebuilds the rollup for every affected date (including dates that
  disappeared in a restatement). Imports are therefore idempotent.
  **Partitioning (PostgreSQL):** `cost_line_items` is `PARTITION BY RANGE (charge_date)`
  with a DEFAULT partition; `costlens migrate` creates monthly partitions 36 months
  back to 12 ahead (`costs/partitions.py`), each with the same forced RLS policy and
  **no direct access for the app role** (only through the parent, where RLS applies).
  Composite primary key (id UUID, charge_date). Indexes: (org, date), (org, source,
  period), (org, resource). Alembic autogenerate ignores partitions and Procrastinate
  tables (`db/schema_filter.py`). Retention policies: detailed lines 3 years (1–10),
  daily summaries 7 years (2–10). Demo seeder `costs.aws`: 5 accounts, 20 service
  streams, 90 days, seasonality, growth, a deployment step change, a Redshift anomaly,
  Savings Plan fee, tax, imperfect tags, resource-level EC2 lines.
- *Faster PostgreSQL tests.* Schema migrated once per run; between tests every table is
  truncated; any test that changes the schema (downgrade) triggers a full rebuild.

- *AWS connections (2.2).* `cloud_connections` (tenant, RLS, audited): provider, name,
  status (pending/active/error), a per-connection **external ID** (`costlens-…`, unique),
  role ARN, account id, linked `cloud_accounts` row, CUR bucket/prefix, last check/error.
  **No AWS credentials are stored**: CostLens's own principal
  (`COSTLENS_AWS_PRINCIPAL_ARN`, credentials from the default AWS chain) assumes the
  customer's role with STS + external ID. `clouds/aws/policy.py` generates the
  CloudFormation (JSON) and Terraform for role `CostLensReadOnly`: trust = our principal
  + `sts:ExternalId`; inline policy = read-only billing/cost, recommendations, resource
  metadata and metrics; explicit **Deny** on secret values, KMS decrypt, SSM parameters,
  DynamoDB item reads, RDS Data API and Lambda invoke; S3 object reads only on the CUR
  prefix. A test fails if any write verb ever appears. `clouds/aws/client.py`:
  `AwsGateway` protocol; `Boto3Gateway` (assume role, 1-hour sessions cached until 5
  minutes before expiry, AWS errors mapped to safe codes); tests use `FakeAws` or
  botocore's Stubber. API `/api/v1/connections` (list for everyone; create, templates,
  verify, update, delete for admins). Verification checks the account in the ARN equals
  the caller identity, refuses an account already connected, creates/links the
  `cloud_accounts` row and marks it as billing account if it is the organization's
  management account. Screen: Settings → Cloud accounts (two-step connect dialog).

- *Cost Explorer import (2.3).* `clouds/aws/cost_explorer.py`: per billing month one
  DAILY query grouped by LINKED_ACCOUNT + SERVICE with UnblendedCost (→ billed) and
  AmortizedCost (→ effective), following `NextPageToken` (capped at 50 pages); charge
  category from the service name/sign (Tax, Credit, Savings Plans/RI purchase → Purchase
  + Committed, else Usage); zero rows skipped; Cost Explorer's `Estimated` flag kept in
  `extras`; linked-account names saved to `cloud_accounts`. Source key
  `aws_ce:<connection id>` so re-imports replace. Jobs: `costs.import_cost_explorer`
  (connection, months 1–13) and `costs.schedule_imports` every 6 hours for active AWS
  connections **without** a CUR bucket (CUR, when configured, is the more detailed
  source; never both, so no double counting). A revoked role marks the connection
  `error`. API: `POST /connections/{id}/import` ("collect now", admins),
  `GET /connections/{id}/imports` (history). Cost Explorer costs ~USD 0.01/request on the
  customer's bill: ~2 requests per connection per run. `AwsGateway.client()` added.

- *CUR 2.0 import (2.4).* `clouds/aws/cur.py`. Data Exports layout
  `<prefix>/<export>/data/BILLING_PERIOD=YYYY-MM/*.parquet` (metadata/manifests ignored).
  Per billing period (last 3 months by default): list files (paginated), compare their
  key+ETag list with `connection.settings.cur_etags` and skip unchanged periods unless
  forced; otherwise download each file to a temporary file and stream it with pyarrow
  in 50,000-row batches (only the needed columns), aggregate hourly rows to one line per
  day and key (account, service, region, zone, resource, usage type, operation, line
  item type, tags, currency), then replace the period (`aws_cur:<connection>`).
  Amortisation: SP covered usage → SP effective cost; SP negation → 0; SP recurring fee
  → unused commitment; SP upfront fee → 0; RI discounted usage → reservation effective
  cost; RI fee → unused amortised upfront + unused recurring; everything else →
  unblended. Categories: Tax; credits/refunds/discounts → Credit; fees → Purchase +
  Committed; covered/discounted → Usage + Committed; Spot usage → Dynamic. Tags:
  `user_x` → `x`, `aws_*` kept. List cost = public on-demand cost. Service name from the
  `product` map (`product_name`), else product code. Scheduler: CUR when the connection
  has a bucket, otherwise Cost Explorer; "collect now" follows the same rule (forced).

- *File imports (2.5).* Upload = raw request body (`POST /api/v1/imports/files?filename=`,
  max 500 MB, no multipart library needed) → stored under `uploads/<org>/<id>/`. Type
  sniffed (Parquet magic bytes, else CSV with BOM handling). FOCUS 1.0–1.2 detected when
  `ChargePeriodStart, BilledCost, BillingCurrency, ServiceName` exist; otherwise a custom
  CSV with a suggested mapping (synonyms for common Azure/GCP/SaaS exports). Custom
  mapping: date, cost, service (required), effective cost, currency column or default,
  account, region, resource, usage type, quantity/unit, charge category (else inferred
  from sign), tag columns (`_tag_columns`), optional strftime date format. Forgiving
  value parsing (`costs/parsing.py`: thousands separators, currency symbols,
  parentheses negatives; ISO dates or a given format; FOCUS `Tags` JSON). `validate` =
  dry run on 10,000 rows with a report (valid/total rows, first 25 problems with row
  number, totals per currency, date range, billing periods). `import` = job
  `costs.import_file`: all rows; refused when >1% fail unless `allow_partial`; hourly rows
  summed to daily (`focus.aggregate_daily`); one replace per billing period with source
  `file:<id>` (re-import replaces). Delete removes the costs too and rebuilds the rollup.
  AWS connections can read **FOCUS 1.0 exports from S3** (`export_format: focus`,
  source kind `aws_focus`). `file_imports` table: tenant, RLS, audited.

- *Spend analytics (2.6).* `costs/analytics.py` on `cost_daily`, one currency at a time
  (organisation display currency by default; other currencies listed separately until
  2.11) and metric `effective` (amortised, default) or `billed`.
  **Forecast:** month-to-date actual + for each remaining day the mean of the last 14
  days of non-recurring spend × that weekday's factor (from 28 days) + recurring charges
  (purchases, tax) that occurred last month on a later day-of-month and not yet this
  month; range = ±1.645 × daily residual std × √remaining days (low never below MTD).
  "Data through" = latest charge date (providers lag ~1 day). **Series:** top N groups
  (default 7) + "Other (n)", by day or month (month bucket via extract(year/month),
  portable). **Movers:** last N days vs the N before by dimension, sorted by absolute
  change, status new/gone/changed; purchases and tax excluded by default (else a
  monthly Savings Plan payment always tops the list). API `/api/v1/costs/summary|daily|
  monthly|movers`. **UI:** Costs page (metric, grouping, range; tiles; daily stacked
  chart; monthly chart with the forecast remainder as a dashed segment; movers table
  with arrows + text, never colour alone), overview tiles. Charts: ECharts (core +
  bar/line, SVG renderer) in `components/chart/echart.tsx`, palette from CSS variables
  `--series-1..8` (the dataviz reference palette, validated for light and dark: adjacent
  CVD ΔE ≥ 9.1/8.4, normal-vision ≥ 19.6; low-contrast slots get relief via legend +
  "Show table"). Costs page is lazy-loaded (ECharts in its own 552 kB chunk).

- *Anomalies (2.7).* `costs/anomalies.py`: per series (total, each service, each
  account; usage/adjustment spend only, so purchases/tax never alarm) and recent day:
  expected = 28-day median × weekday factor; spread = 1.4826 × MAD of residuals (floor 2%
  of expected); flag when actual > expected + k·spread (k: high 2.5 / medium 3 / low 4)
  **and** extra ≥ minimum impact (default 50) **and** ≥ 10% of expected. Only increases.
  Needs ≥14 days of history. **One incident, one entry:** total/account findings are
  dropped when same-day service findings explain ≥70% of them (account share computed
  from that service's spend in the account), and consecutive days of the same series
  extend one *episode* (`day`…`last_day`, `days`, cumulative `impact`, `peak_impact`).
  `cost_anomalies` (tenant, RLS, audited, retention 3 years) with status open /
  acknowledged / expected / resolved + note. Settings in `organizations.settings.alerts`
  (on/off, sensitivity, minimum impact). Job `costs.detect_anomalies` runs after every
  successful import (CE, CUR, file) and daily for all organisations; new anomalies are
  emailed once to owners/admins (`costs/alerts.py`; demo org never emails). API:
  `GET/PATCH /api/v1/costs/anomalies`, `GET/PUT /api/v1/org/alert-settings`. UI: card on
  Costs and overview (account names shown), alert settings card. Audit action nouns now
  singularise `-ies` tables correctly (`cost_anomaly.updated`).

- *Cost explorer (2.8).* `costs/explorer.py`: up to two group-by dimensions from
  service, account, region, charge/pricing category, usage type, resource, `team`,
  `project` (aliases for configurable tag keys in `organizations.settings.dimensions`,
  default `team`/`project`) and `tag:<key>` (key validated by pattern and always bound as
  a JSON-path parameter); filters on the same set ("(untagged)"/"(none)" = NULL). Reads
  `cost_daily` when possible, `cost_line_items` when tags/resources/usage types are
  involved (`source` tells the UI). Output: groups (total, share, previous equal period,
  change), top-N series + Other by day/month, resource details (name, type, service,
  region, account) when grouped by resource; accounts labelled "Name (id)" while keys
  stay raw ids. Tag keys with coverage via `json_object_keys` (PostgreSQL) / `json_each`
  (SQLite). Saved views (`saved_views`, tenant, RLS, audited): private or shared; owner
  or admins may edit; relative ranges stored as `days`. API: `POST /api/v1/costs/explore`,
  `GET /api/v1/costs/dimensions/{dim}/values`, `GET /api/v1/costs/tags`,
  `GET/PUT /api/v1/org/dimension-settings`, `/api/v1/views` CRUD. UI: `/costs/explore`
  (lazy-loaded), linked from Costs. `create_app` registers routers in a loop.

- *Change timeline (2.9).* `change_events` (tenant, RLS; retention 2 years): when,
  source (api/github/cloudtrail/manual), kind (deployment/infrastructure/config/incident/
  other), title, service/account/region/resource/environment, url (http(s) only), actor,
  external id (unique per source → idempotent re-delivery). Sources: `POST /api/v1/changes`
  (members; API keys from 8.1 make it usable from CI); **GitHub webhooks** via
  `inbound_webhooks` (tenant, audited, secret encrypted and shown once): HMAC-SHA256
  `X-Hub-Signature-256` over the raw body, `deployment_status` success and published
  `release` → deployment events, others ignored; **CloudTrail** `LookupEvents`
  (read-only, ReadOnly=false filter) in the regions where the account has spend, last 24 h,
  ≤5 pages per region, only cost-relevant write events, mapped to cost service names
  (hourly job). `cost_effect()`: average daily usage cost (of the event's service/account if
  known) for 7 days after vs before. `related_events()`: changes from 3 days before an
  anomaly to its last day on the same service/account (or unscoped) → "possible cause".
  API: `GET /api/v1/changes` (with effect), `DELETE`, `GET /api/v1/costs/timeline`,
  `GET /api/v1/costs/anomalies/{id}/changes`, webhook CRUD under `/api/v1/org/webhooks`.
  UI: "What changed" card (cost line with dashed change markers + list with effect,
  record-a-change dialog), possible cause on anomalies, GitHub webhook setup in Settings →
  Cloud accounts. Demo: deployment explaining the EC2 step change, ResizeCluster
  explaining the Redshift spike, two more.

- *Data quality (2.10).* `costs/quality.py` over taggable spend (Usage/Adjustment line
  items; tax, credits and purchases cannot be tagged): coverage per required tag
  (`organizations.settings.data_quality.required_tags`, default team/project/env) with top
  untagged services; unallocated spend = no team tag (the team alias); top untagged
  resources; inconsistent tag keys and values found by a canonical form (lower-case,
  separators and camelCase boundaries removed: `CostCenter` = `cost-center`), suggesting
  the highest-spend spelling. `costs/tagrules.py`: key and value merge rules in
  `organizations.settings.tag_rules`, applied on every import and re-applied to existing
  line items by job `costs.reapply_tag_rules` (keyset batches; rollup has no tags so it
  is unaffected). API: `GET /api/v1/costs/data-quality`, required-tag settings, tag rules
  get/put/apply. UI: `/costs/data-quality` (coverage tiles with bars + numbers,
  unallocated, "Merge into …" one-click rules, untagged resources, required tags editor).

- *Currencies (2.11).* `exchange_rates` (global reference data: day, currency, units per
  1 EUR, source ecb/carried; every calendar day present, weekends/holidays carried
  forward). `costs/fx.py`: ECB feed parsed with strict regular expressions (no XML parser,
  so no entity tricks), first run loads full history, later the 90-day feed; daily job
  `fx.refresh` (16:20 UTC). Conversion is a SQL expression: same-currency rows pass
  through untouched; others multiply by that day's rate ratio via correlated lookups; no
  rate → NULL → excluded from sums and reported as "not converted" (summary
  `other_currencies` now means exactly that). Applied in summary, forecast, series,
  movers, explorer and data quality (anomalies stay per original currency). Display
  currency: organisation default, overridable per viewer (remembered in the browser).
  Performance note: correlated lookups only run for foreign-currency rows; if large
  multi-currency tenants appear, materialise converted amounts in `cost_daily`.

- *Permission self-check (2.12).* `clouds/aws/permissions.py`: 17 harmless read probes
  (small pages) covering organisations, billing exports, the CUR bucket prefix, EC2
  instances/volumes/addresses/NAT/snapshots, load balancers, RDS, CloudWatch, Savings Plans,
  Compute Optimizer, Cost Optimization Hub, Trusted Advisor, CloudTrail, EKS — each mapped
  to the feature it powers. Status: ok / missing (access denied) / unavailable (plan or
  opt-in, e.g. Trusted Advisor without Business support) / error / skipped. Cost Explorer
  is deliberately not probed (paid per call). Report includes the missing actions as a
  ready-to-paste policy statement; stored in `connection.settings.permissions`.
  API `POST/GET /api/v1/connections/{id}/permissions-check`; "Check permissions" dialog.

- *Resource inventory (2.13).* `cloud_resources` (tenant, RLS): provider, account,
  region, service (cost service name), type, resource id **as billing data spells it**
  (instance/volume ids, ARNs for RDS/ELB, allocation ids for addresses), name, state,
  owner (owner tag → `Owner` → team tag; owner tag configurable), created/first/last
  seen, tags, attributes (instance type, size, engine, attachments…).
  `inventory/aws.py` read-only collectors (EC2 instances, EBS volumes, Elastic IPs, NAT
  gateways, own snapshots, load balancers, RDS) with pagination, run per connection
  every 6 h in the regions where the account has spend; a failing collector (missing
  permission, disabled region) is skipped and that region's run counts as incomplete;
  only complete runs mark unseen resources `deleted` (kept for history). 30-day cost per
  resource joined from billing line items, converted to the organisation currency;
  optional opt-in Cost Explorer resource-level fallback for EC2 without CUR
  (`settings.ce_resource_level`, stored on the inventory row only — never double
  counted). `cost_hourly` (tenant, RLS, retention 14 days) captured from hourly CUR rows
  by (hour, account, service). API: `GET /api/v1/resources` (type/state/search/sort,
  counts by type), `GET /api/v1/resources/{id}` (daily cost), `GET /api/v1/costs/hourly`.
  UI: `/costs/resources` (type chips, search, state, sort, detail dialog with chart and
  attributes/tags), "Last 72 hours" card on Costs when hourly data exists. Demo seeder adds
  matching resources plus planted waste (unattached volume and IP, stopped instance, old
  snapshot, legacy load balancer, old RDS engine with Multi-AZ) for section 4.
- *Registration safety.* Demo seeders and job modules are loaded by module name lists
  (`SEEDER_MODULES`, `JOB_MODULES`) so automatic unused-import clean-ups cannot drop them;
  guard tests assert every seeder and job is registered, and demo tests now fail instead of
  skipping.

## 5. Open questions for the owner

| # | Question | Blocks | Status |
|---|---|---|---|
| Q1 | **Product name** — decided: **CostLens** (2026-11-05). Remaining for you: register costlens.com (no DNS records on 2026-11-05, so likely unregistered; .io and .cloud are taken), trademark search on IP India (classes 9 & 42), USPTO and EUIPO — "lens" is a common product word, so check for similar marks in software/FinOps — and claim the GitHub org and social handles. | nothing (name in use) | decided; checks pending |
| Q2 | **Plans and prices**: plan names, INR and USD prices, trial length, limits (cloud spend under management, clusters, users, data retention), annual discount. | B.1, B.3, B.4, B.9 pricing page | open |
| Q3 | **Payment accounts**: Razorpay and Stripe accounts (business KYC as a sole proprietorship), GSTIN for GST invoices, place of supply rules, invoice numbering format. | B.3, B.4 | open |
| Q4 | **Transactional email provider** for SaaS (paid service): e.g. Amazon SES, Postmark, Resend — or SMTP only? Self-hosted will use customer SMTP. | 1.9 (SaaS sending) | open |
| Q5 | **Exchange-rate source** — built with the free **ECB** euro reference rates (≈30 currencies incl. INR, USD; working days, carried over weekends) behind a pluggable provider (`COSTLENS_FX_PROVIDER=ecb\|none`). Confirm ECB is fine, or say if finance needs RBI reference rates (INR) or a paid feed. Fixed monthly finance rates come with 2.17 (manual overrides). | nothing (2.11 done with ECB) | proposed, awaiting confirmation |
| Q6 | **Legal**: business address, contact emails (legal@, support@, security@), governing law/jurisdiction (suggest Tamil Nadu, India), and lawyer review of LICENSE, EULA, terms, privacy (DPDP Act 2023 + GDPR), DPA, SLA targets, refund policy. | B.10, final LICENSE | open |
| Q7 | **SaaS hosting design**: AWS region(s) (suggest ap-south-1 Mumbai + us-east-1), ECS Fargate vs EKS, budget. | D.6 | open |
| Q8 | ~~Docker Desktop was not running in sessions 1 and 2, so the image build and PostgreSQL test run were not verified locally (CI does both). Start Docker if you want them verified next session. **Now also needed for 1.2 (row-level security), which can only be tested on PostgreSQL** — without Docker I can build it but only CI will prove it.~~ Resolved 2026-11-05: Docker running; image, compose and PostgreSQL tests verified. | — | resolved |
| Q10 | **Google OAuth client for the SaaS** (free): create a Google Cloud project for Yugiverse, configure the OAuth consent screen (app name CostLens, logo, privacy/terms links, support email; Google verification needed for external users) and an OAuth client (Web) with redirect `https://<saas domain>/api/v1/auth/callback/google`. Give me the client ID; the secret goes only into the hosting secrets. Self-hosted customers create their own. | SaaS sign-in go-live | open |
| Q11 | **AWS collector principal for the SaaS** (free; part of Q7 hosting): an IAM role in Yugiverse's AWS account that assumes customers' read-only roles. Its ARN goes in `COSTLENS_AWS_PRINCIPAL_ARN` and appears in every customer template, so pick a long-lived name (e.g. `arn:aws:iam::<yugiverse-account>:role/costlens-collector`). | SaaS AWS go-live | open |
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

### 2026-11-05 — Session 6: 1.8 secrets at rest, 1.5 more identity providers
- **1.8 done** (built before 1.5 because SSO client secrets need it): `SecretBox`,
  `EncryptedString`, `costlens rotate-keys`, local dev key file. 17 tests incl. SSRF guard.
- **1.5 done:** Entra (single and multi-tenant), Okta, generic OIDC per instance;
  organisation SSO model + migration 0004 (RLS) + admin API; `require(permission)`
  dependency (401 / 409 no organisation / 403); SSRF guard for customer URLs.
  18 tests: config parsing/validation, Entra multi-tenant sign-in + tenant/issuer check +
  no-trust takeover refusal, Okta, admin CRUD + test endpoint, member 403, other org 404,
  sign-in through an org provider, disabled/unknown provider, internal-network issuer
  blocked before any request, validation, secret encrypted at rest.
- Results: SQLite 152 passed / 15 skipped; PostgreSQL 167 passed; ruff + pyright clean;
  frontend 13 passed, oxlint + tsc clean, build OK.
- Next: 1.6 CSRF protection, then 1.7 rate limiting.

### 2026-11-05 — Session 6b: 1.6 CSRF
- **1.6 done:** CSRF middleware, CSRF cookie at sign-in, `csrf_token` in `/me`, web
  client sends the header; test helpers sign requests. 7 backend tests (valid, missing,
  wrong token for POST/PATCH/DELETE, cross-site / foreign origin / null origin rejected
  even with a token, same origin allowed, non-cookie requests untouched, `/me` token) +
  1 frontend test. Fixed the frontend fetch mock (fresh Response per call).
- Results: SQLite 159 passed / 15 skipped; PostgreSQL 174 passed; frontend 14 passed;
  all linters, type checks and the build clean.
- Next: 1.7 rate limiting.

### 2026-11-05 — Session 6c: 1.7 rate limiting
- **1.7 done:** middleware, memory + PostgreSQL limiters, migration 0005
  (`rate_limit_buckets`), cleanup job + `load_all_jobs()` job loader, settings. 6 tests
  (windows, anonymous limit + headers, tighter sign-in limit, per-session user/write
  limits, disable switch, PostgreSQL limiter shared between two "workers" + cleanup).
- Results: SQLite passed (PG-only skipped); PostgreSQL 180 passed; frontend 14 passed;
  all linters, type checks and the build clean.
- Next: 1.9 email invitations.

### 2026-11-05 — Session 6d: 1.9 invitations and email
- **1.9 done:** email layer + `email.send` job, invitation model (migration 0006, RLS),
  service, API; worker now configures encryption and email. 10 tests (email with link
  and escaping, accept + switch org, wrong account refused, expired, revoked, re-invite
  replaces, role permissions, existing member, invalid token, other org isolation,
  SMTP MIME building).
- Results: SQLite 174 passed / 16 skipped; PostgreSQL 190 passed; frontend 14 passed;
  all linters, type checks and the build clean.
- Next: 1.10 organisation switcher + members management (API now, UI with 1.13).

### 2026-11-05 — Session 6e: 1.10 organisation API
- **1.10 API done** (UI with 1.13): create organisation (+ switch), switch, get/update
  organisation (name, default currency; demo org read-only), list members, change role,
  remove/leave (clears active org), transfer ownership (old owner becomes admin).
  7 tests incl. a removed member losing access on their next request.
- Results: SQLite 181 passed / 16 skipped; PostgreSQL passed; frontend 14 passed; all clean.
- Next: 1.11 audit log.

### 2026-11-05 — Session 6f: 1.11 audit log
- **1.11 capture + API done** (screen with 1.13): model + migration 0007 (RLS), automatic
  recorder, explicit `record()`, actor context, admin API with filters/pagination/CSV
  export, `REVOKE UPDATE` for the app role. 9 tests (org/membership events, before/after
  + actor + IP + request id, secret redaction across create/update/test/delete, no-op
  updates ignored, filters + pagination + bad cursor, admin-only, org isolation, CSV
  formula neutralisation, PostgreSQL: app role cannot UPDATE audit rows).
- Found and fixed: a test reached for the real network (SSO "test connection"); tests are
  now network-proof (autouse transport block + offline default client).
- Results: SQLite 189 passed / 17 skipped; PostgreSQL 206 passed; frontend 14 passed;
  all linters, type checks and the build clean.
- Next: 1.12 demo organisation with realistic sample data.

### 2026-11-05 — Session 6g: 1.12 demo framework
- **1.12 framework done** (data arrives with each feature): seeder registry, ensure/reset/
  join, API, read-only enforcement, nightly reset job, CLI, reserved slugs. 7 tests
  (order + determinism across reset, duplicate names, reset keeps members and replaces
  data, join API idempotent, read-only even for demo admins while reading/leaving work,
  disable switch, reserved slugs).
- Results: SQLite 196 passed / 17 skipped; PostgreSQL 213 passed; frontend 14 passed; all clean.
- Next: 1.13 sign-in, onboarding and account screens (+ 1.10/1.11 screens).

### 2026-11-05 — Session 6h: 1.13 screens (+ 1.10, 1.11 screens)
- **1.13 done**, finishing the screens for **1.10** and **1.11**: session handling,
  route guards, sign-in, invitation, welcome/onboarding, organisation switcher, account
  menu, demo banner, settings (general, members, SSO, audit log). 21 new frontend tests.
- Added local development sign-in (opt-in, local mode only) + 2 backend tests.
- **Checked in a real browser** (local sign-in → create organisation → members →
  audit log; phone width + dark mode). This found and fixed two real problems:
  (1) CSRF origin check rejected the Vite dev server on another port → any localhost
  origin accepted in local mode only (2 tests); (2) the local signing key changed on
  restart, breaking CSRF for existing sessions → persisted in the data dir, and the web
  client now uses the CSRF token from `/me`.
- Results: SQLite 200 passed / 17 skipped; PostgreSQL 217 passed; frontend 35 passed;
  all linters, type checks and the build clean.
- Polish noted for 9.3: audit "Details" shows raw ids for created rows.
- Next: 1.14 data export and account/organisation deletion.

### 2026-11-05 — Session 6i: 1.14 privacy rights
- **1.14 done:** personal export, account deletion with pseudonymised audit trail,
  organisation export (ZIP job, secrets excluded, 7-day expiry), organisation deletion
  with 30-day restore and purge, storage abstraction, job context (also used now by the
  demo and rate-limit jobs), migration 0008 (`data_exports`, RLS), column-level audit
  grant. Screens: "Your account" tab (download my data, delete account) and an owner
  zone (exports, delete organisation). 10 backend + 3 frontend tests.
- Fixed while testing: audit rows written by the deletion cascade kept the email →
  pseudonymisation now runs after the delete.
- Results: SQLite 210 passed / 17 skipped; PostgreSQL passed; frontend 38 passed; all clean.
- Next: 1.15 data retention settings and purge.

### 2026-11-05 — Session 6j: 1.15 retention
- **1.15 done:** retention registry + audit-log policy, per-organisation settings
  (migration 0009), API, nightly purge job, settings card. 4 backend tests (per-org purge
  keeps exactly what each setting allows, bounds/unknown kinds/clamping, registry
  validation, API permissions + audited change) + 1 frontend test. Audit recorder now
  keeps JSON values structured.
- Results: SQLite 214 passed / 17 skipped; PostgreSQL 231 passed; frontend 39 passed; all clean.
- **Section 1 is now complete** except 1.12's per-feature demo data (added with each feature).
- Next: 2.1 canonical cost model.

### 2026-11-05 — Session 7: 2.1 cost model
- **2.1 done:** cost models + migration 0010 (partitioned table, default partition, RLS on
  all cost tables), idempotent ingest with daily rollup, monthly partition maintenance in
  `costlens migrate`, retention policies, realistic demo cost data. 13 tests (rollup,
  restatement replaces and removes vanished days, independent sources/periods, org
  isolation, validation ×4, 12,000-row batched import, date-based retention, demo data,
  PostgreSQL: rows routed to monthly partition, partition RLS forced, app role denied on
  the partition).
- Fixed along the way: partitioned indexes in `pg_inherits` broke the grant step; the RLS
  coverage check ignored partitioned tables; schema comparison saw partitions as drift.
- PostgreSQL test run 82 s → 25 s (migrate once, truncate between tests).
- Results: SQLite 225 passed / 18 skipped; PostgreSQL 243 passed; frontend 39 passed; all clean.
- Next: 2.2 AWS connection (read-only role with external ID).

### 2026-11-05 — Session 7b: 2.2 AWS connection
- **2.2 done:** read-only IAM policy + CloudFormation/Terraform generators, gateway
  (boto3 + fake), connection model (migration 0011, RLS), service, API, settings screen.
  15 backend tests (policy has no write verbs and denies data access, CUR access scoped to
  prefix, trust requires external ID, ARN validation ×5, full connect flow, wrong external
  ID recorded, account mismatch, duplicate account, permissions + isolation, update/delete
  audited, not-configured error, real boto3 gateway with Stubber: error mapping + caching)
  + 1 frontend test. New question Q11 (SaaS collector principal).
- Results: SQLite 239 passed / 18 skipped; PostgreSQL 257 passed; frontend 40 passed; all clean.
- Next: 2.3 Cost Explorer import.

### 2026-11-05 — Session 7c: 2.3 Cost Explorer import
- **2.3 done:** importer, jobs (+ scheduler), collect-now and history API, UI button.
  12 backend tests (billing periods, classification ×5, two-month import with pagination,
  names, estimated flag and skipped zero rows; idempotent re-import; amortised = effective;
  revoked role; collect now + history + validation; scheduler skips CUR connections) +
  1 frontend test, using a fake Cost Explorer.
- Results: SQLite 251 passed / 18 skipped; PostgreSQL 269 passed; frontend 41 passed; all clean.
- Next: 2.4 CUR 2.0 import from S3 (Parquet).

### 2026-11-05 — Session 8: 2.4 CUR 2.0 import (resumed after a usage-limit pause)
- **2.4 done:** CUR 2.0 Parquet importer (streaming, daily aggregation, amortisation,
  ETag change detection), `costs.import_cur` job, scheduler and collect-now choose CUR vs
  Cost Explorer. 9 tests with real Parquet files (CUR 2.0 columns, map-typed tags and
  product) served by a fake S3: hourly→daily aggregation across files, manifests
  ignored; SP/RI/tax/credit/Spot amortisation and categories with totals checked;
  unchanged files skipped, changed re-imported, forced import; previous months and
  foreign prefixes; missing bucket error; tag normalisation; period list.
- Results: SQLite 259 passed / 18 skipped; PostgreSQL 277 passed; frontend 41 passed; all clean.
- Next: 2.5 CSV and FOCUS file import.

### 2026-11-05 — Session 8b: 2.5 CSV and FOCUS import
- **2.5 done:** parsers, FOCUS mapping + daily aggregation, custom CSV mapping,
  upload/preview/validate/import/delete API, import job, FOCUS-from-S3 for AWS, migration
  0012, upload screen in Settings → Cloud accounts. 16 backend tests (parsers, mapping
  suggestions, FOCUS CSV + Parquet end to end, custom CSV with tags/date format/credits,
  mapping checks, row-level error report + 1% refusal + partial import, idempotent
  re-import + delete, bad uploads + permissions, FOCUS from S3) + 1 frontend test.
- Fixed while testing: unmapped charge category ignored negative amounts (credits).
- Results: SQLite 275 passed / 18 skipped; PostgreSQL 293 passed; frontend 42 passed; all clean.
- Next: 2.6 spend API, charts, biggest movers and month-end forecast.

### 2026-11-05 — Session 8c: 2.6 spend, movers, forecast
- **2.6 done:** analytics (summary, forecast, series, movers), costs API, Costs page,
  overview tiles, theme-aware chart component, lazy-loaded charts. 7 backend tests
  (forecast maths incl. recurring tax and range, no-data case, summary + other
  currencies + validation, daily top-N + Other, monthly series, movers + purchases
  switch, empty org isolation + range limit) + 5 frontend tests.
- **Checked in a real browser with demo data** (desktop, phone, dark). Found and fixed:
  (1) a stale session cookie (from an old database) blocked local sign-in through the
  CSRF check → 401 responses now clear the stale cookie and sign-in endpoints skip the
  token check (origin checks remain); test added. (2) Movers table clipped at laptop
  width → layout breakpoint. (3) Monthly chart showed empty months before data → trimmed.
  (4) A monthly Savings Plan payment dominated movers → excluded by default (found by test).
- Results: SQLite 283 passed / 18 skipped; PostgreSQL 301 passed; frontend 47 passed; all clean.
- Next: 2.7 anomaly detection and alerts.

### 2026-11-05 — Session 8d: 2.7 anomalies and alerts
- **2.7 done:** detection, episodes, duplicate suppression, alerts by email, settings,
  API, UI, demo seeder (finds the planted Redshift spike), detection after imports,
  migration 0013 (`cost_anomalies`, `organizations.settings`). 12 backend tests (weekday
  baseline, spike vs normal weekend, minimum impact + sensitivity, idempotent, too little
  history, alert email recipients + once only, alerts off, API permissions + status +
  settings + audit, file import triggers detection + email, demo, increase spread across
  services reported for the account, consecutive days form one episode) + 2 frontend.
- **Browser check found** the demo's one incident shown as 18 anomalies (6 days × service,
  account, total) and raw account ids → episodes, explained-duplicate suppression and
  account names added; now 1 entry ("6 days, extra so far +$4,449").
- Fixed: audit action noun for `-ies` tables.
- Results: SQLite 296 passed / 18 skipped; PostgreSQL 314 passed; frontend 49 passed; all clean.
- Next: 2.8 breakdowns by account, service, region, tag, team, project; cost per resource; saved views.

### 2026-11-05 — Session 8e: 2.8 cost explorer
- **2.8 done:** explorer engine, tag keys, filter values, team/project tag settings,
  saved views (migration 0014), explorer page. 11 backend tests (rollup grouping with
  share and previous-period change, tag grouping with untagged, team alias + setting,
  two dimensions + filters incl. untagged, daily series + Other + monthly, cost per
  resource with details, validation incl. tag-key injection attempt and range limit,
  values + tag keys with coverage, saved views privacy/sharing/permissions, account
  names) + 3 frontend tests.
- Browser check: tag keys discovered from demo data, Team · Account works; added account
  names and removed duplicate tag options. Fixed: SQLite rejected the table-valued alias
  form used for tag keys.
- Results: SQLite 306 passed / 18 skipped; PostgreSQL 324 passed; frontend 52 passed; all clean.
- Next: 2.9 change timeline linking cost changes to deployments and infrastructure changes.

### 2026-11-05 — Session 8f: 2.9 change timeline
- **2.9 done:** change events + inbound webhooks (migration 0015), API/GitHub/CloudTrail
  sources, cost effect, anomaly linking, timeline endpoint and card, webhook settings, demo
  events. 8 backend tests (idempotent record, cost effect, related events for an anomaly
  incl. API, create/list/timeline + URL validation + permissions, GitHub flow with bad
  signature / recorded / duplicate / ignored + org isolation, GitHub mapping, CloudTrail
  import + de-dup + read-only lookup filter, demo) + 3 frontend tests.
- Fixed: SQLAlchemy `distinct()` warnings; lucide no longer ships brand icons.
- Results: SQLite 314 passed / 18 skipped; PostgreSQL 332 passed; frontend 55 passed; all clean.
- Next: 2.10 data quality (missing/inconsistent tags, unallocated spend, tag normalisation).

### 2026-11-05 — Session 8g: 2.10 data quality
- **2.10 done:** quality report, tag normalisation rules (at import + re-apply job),
  settings, API, page. 4 backend tests (canonical form + rule application, full report:
  coverage, unallocated, untagged resources, key and value variants; rules fix existing
  data and normalise new imports; settings validation + permissions) + 2 frontend tests.
- Results: SQLite passed; PostgreSQL 336 passed; frontend 57 passed; all clean.
- Next: 2.11 multi-currency (exchange-rate source is Q5 — will build with a pluggable
  provider and ECB reference rates as the free default, pending your answer).

### 2026-11-05 — Session 8h: 2.11 multi-currency
- **2.11 done** (with ECB as proposed default; Q5 updated): rates table (migration 0016),
  ECB provider + fill-forward store + refresh job, SQL conversion across analytics /
  explorer / quality, unconverted reporting, display-currency picker. 7 backend tests
  (parse + fill forward + cross rates, hostile XML ignored, history-then-90-day fetch,
  outage tolerated, explorer converts USD/INR to INR and USD, summary converts and reports
  JPY as unconverted, rates are global) + 1 frontend test.
- Results: SQLite 325 passed / 18 skipped; PostgreSQL 343 passed; frontend 58 passed; all clean.
- Next: 2.12 AWS permission self-check.

### 2026-11-05 — Session 8i: 2.12 permission self-check
- **2.12 done:** probes, classification, policy snippet, stored report, API, dialog.
  3 backend tests (mixed results incl. denied / unavailable / error / bucket prefix,
  snippet content, stored report; probes are read-only and never call Cost Explorer;
  revoked role) + 1 frontend test.
- Results: SQLite 328 passed / 18 skipped; PostgreSQL 346 passed; frontend 59 passed; all clean.
- Next: 2.13 resource inventory.

### 2026-11-05 — Session 8j: 2.13 resource inventory
- **2.13 done:** inventory + hourly models (migration 0017), AWS collectors, upsert and
  deleted detection, resource costs (converted), CE fallback, hourly capture in CUR import,
  API, Resources page, hourly card, demo inventory. 8 backend tests (collector mapping incl.
  owners and unattached states; deleted only after complete runs; API costs, filters,
  search, detail, isolation; CE fallback; hourly capture; hourly API; demo; direct collector)
  + 1 frontend test.
- **Found and fixed a serious regression:** an earlier automatic lint fix had removed the
  imports that register demo seeders, so the demo organisation had silently lost its cost,
  anomaly and change data, and the tests that should have noticed were skipping. Loaders now
  import by name, guard tests were added, demo tests fail instead of skip.
- Also fixed: resource costs were not currency-converted.
- Results: SQLite 338 passed / 18 skipped; PostgreSQL 356 passed; frontend 60 passed; all clean.
- Next: 2.14 virtual tags.

### 2026-11-05 — Session 8k: 2.14 virtual tags
- **2.14 done:** `virtual_tags` table + `vtags` JSON column on line items (migration 0018).
  Rules: AND across conditions (account, service, region, tag key=value, resource or usage
  type contains), OR within a list; first matching rule wins; a real tag wins unless
  "override" is set; otherwise the default value. Computed at import, recomputed by a job
  after every change. `vtag:<key>` works in the explorer and saved views, and the team or
  project alias can point at a virtual tag (data quality uses it too). Preview shows where
  last-30-day spend would land before saving.
- UI: Costs → Virtual tags (list, rule editor, preview); explorer lists virtual tags as
  dimensions. Budgets, allocation and reports will use `vtag:` dimensions when they are built.
- Results: SQLite 342 passed / 18 skipped; PostgreSQL 360 passed; frontend 61 passed; all clean.
- Next: 2.15 anomalies explained in plain words.

### 2026-11-05 — Session 8l: 2.15 anomalies explained in plain words
- **2.15 done:** `costs/explain.py` compares the episode with the 14 days before, broken
  down by account/service, region, usage type and resource; picks the main driver and
  detail; adds the nearest related change. Endpoint
  `GET /api/v1/costs/anomalies/{id}/explanation`; alert emails include the explanation
  for the top 5. UI: "Why did this happen?" on each anomaly with a drivers table.
- **Optional AI wording:** `ai/writer.py` (Anthropic SDK, model from `COSTLENS_AI_MODEL`,
  low effort, server-side refusal fallback, 30 s timeout, one retry). Used only when the
  installation has `COSTLENS_ANTHROPIC_API_KEY` **and** the organisation ticks "Word anomaly
  explanations with AI" (off by default; sends only the summary and drivers, never billing
  files). Any failure or refusal falls back to the template. Tests use fakes, no real calls.
- Demo wording: "Amazon Redshift has cost more than usual from 31 Oct to 5 Nov (6 days)…
  Most of the increase came from account Data Platform… mainly usage type Node:ra3.4xlarge.
  A change shortly before may explain it: "ResizeCluster analytics-warehouse…"".
- Results: SQLite 348 passed / 18 skipped; PostgreSQL 365 passed; frontend 62 passed; all clean.
- Next: 2.16 consolidated multi-cloud view (needs Azure/GCP from section 7, so it will be
  built after them); 2.17 manual FX overrides next.

### 2026-11-05 — Session 8m: 2.17 exchange-rate overrides
- **2.17 done:** `fx_overrides` (org-scoped, RLS, audited; migration 0019): currency pair,
  date range (UI picks a month), rate, note. The shared SQL `converted()` now takes the
  row's `org_id` column and uses, in order: the override for that day, the inverse
  override (1/rate), then the daily reference rate. All six call sites (summary, series,
  explorer, data quality, resources, hourly) get overrides automatically; `fx.rate()` takes
  an optional org. Overlapping overrides for the same pair (either direction) are refused.
- API: `/api/v1/fx/overrides` (list for readers; create/delete for admins) and
  `/api/v1/fx/rate` (rate used vs reference, flags overrides). UI: Settings → General →
  Exchange-rate overrides (table + "1 USD equals 84.50 INR" form).
- Tests: override applies only to its days, both directions, only for its organisation
  (other org unaffected, cannot see or delete it), viewer refused, overlap refused,
  validation; frontend form test.
- Results: SQLite 350 passed / 18 skipped; PostgreSQL 368 passed; frontend 63 passed; all clean.
- Section 2 is complete except 2.16 (needs Azure/GCP connectors from section 7).
- Next: 3.1 Kubernetes agent + ingest API.

### 2026-11-05 — Session 8n: 3.1 Kubernetes agent, ingest API, Helm chart (+ D.4)
- **Backend:** `k8s_clusters`, `k8s_objects`, `k8s_events` (RLS; migration 0020).
  - Cluster token `ckt_<cluster id>_<secret>`: only a SHA-256 of the secret is stored,
    compared in constant time, shown once, rotated by `POST /clusters/{id}/rotate-token`
    (old token stops at once).
  - Admin API `/api/v1/clusters` returns the token plus a ready Helm command. Readers see
    the list with a health value: waiting, ok, silent (no report for 15 min) or outdated
    (agent older than the latest).
  - Agent API `/api/v1/agent/{hello,snapshot,events}`:
    - `hello` returns settings (system namespaces skipped by default, intervals).
    - `snapshot` takes one kind in chunks; the final chunk marks unseen objects deleted.
    - `events` are upserted by UID.
    - Limits: an allow-list of 12 kinds (no Secret or ConfigMap), 5,000 items per call,
      20 MB bodies.
  - Audit: added `__audit_ignore__`, so heartbeat fields (last seen, versions, node count)
    don't flood the audit log.
  - Retention policies: "Kubernetes events" and "Removed Kubernetes objects" (live
    objects are never purged).
- **Agent (`agent/`, package `costlens-agent` 0.1.0):** kubernetes client + httpx only.
  - Paginated list calls; trims every object to an explicit allow-list (container env,
    command, args and annotations are never sent).
  - Namespace include/exclude; events re-sent only when new or their count changed.
  - Chunked snapshots; retries with backoff on 5xx/429/network errors; exits on a
    revoked token.
  - Token read from a mounted file; https required unless `COSTLENS_ALLOW_HTTP=true`.
  - Non-root image (`agent/Dockerfile`, builds; 431 MB, mostly the kubernetes client —
    slimming is a follow-up).
- **Helm chart (`deploy/helm/costlens-agent`):**
  - ClusterRole is get/list/watch only, and has no Secrets, ConfigMaps, exec or logs.
  - Token in a Secret (or `existingSecret`), mounted read-only.
  - Pod hardening: runAsNonRoot, read-only root filesystem, all capabilities dropped,
    seccomp RuntimeDefault.
  - Install fails clearly without a URL or token.
  - `helm lint` is in `make check` and CI. The release workflow builds and signs a
    multi-arch agent image and pushes the chart to GHCR as OCI.
  - The chart registry depends on Q9 (GitHub organisation); `COSTLENS_AGENT_CHART` sets
    the address shown in install commands.
- **UI:** Settings → Kubernetes:
  - Connect a cluster: the Helm command and token are shown once, with copy buttons.
  - Health badges and a detail line (provider, version, nodes, pods, agent version,
    last report).
  - New token and Disconnect buttons.
- **Tests:**
  - 8 backend cluster tests: token only shown once; snapshot and deletion; events
    upsert; bad, rotated and deleted tokens; kind allow-list; roles; isolation;
    silent/outdated health; retention.
  - An end-to-end test where the real agent code (fake Kubernetes API) reports into the
    real app and a secret env value never arrives.
  - 7 agent tests (trimming with a secrets corpus, pagination, filters, chunking,
    retries, revoked token, event de-duplication, config).
  - 1 frontend test.
- Results: SQLite 357 passed / 18 skipped; PostgreSQL 375 passed; agent 7 passed;
  frontend 64 passed; all lint and type checks clean.
- Next: 3.2 cost per cluster/namespace/workload and idle capacity.
