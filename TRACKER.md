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
- [x] 0.4 Operations polish: worker heartbeat + healthcheck (worker currently has none), compose smoke test in CI, migration-safety check (no destructive change without a flag) — *image slimming moved to 0.5*
- [x] 0.5 Slim the image (1.14 GB unpacked: check boto3/botocore data, pyarrow, uvicorn extras) — *1.24 GB → 839 MB*

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
- [x] 2.16 (J) Consolidated view of AWS, Azure, Google Cloud and SaaS connectors together in one currency (after 7.1, 7.2, 7.4; grows as connectors land)
- [x] 2.17 (J) Manual exchange-rate overrides per organisation (e.g. a fixed finance rate per month), taking precedence over daily rates and shown as overrides

### 3. Kubernetes
- [x] 3.1 Agent (Python, read-only RBAC) + ingest API with per-cluster tokens; Helm chart
- [x] 3.2 Cost per cluster/namespace/workload; idle capacity
- [x] 3.3 Requests vs usage (Prometheus or metrics-server); right-sizing never below observed peak (+ headroom)
- [x] 3.4 Incident investigation: events, metrics, logs, recent changes → ranked causes with evidence, fixes, verification steps
- [x] 3.5 Optional AI plain-English summary (anthropic SDK, fake in tests, redaction)
- [x] 3.6 Detection of CrashLoopBackOff, OOMKilled, unhealthy workloads; Slack/Teams notifications
- [x] 3.7 (G) Agent-side redaction: remove secrets (tokens, keys, passwords, connection strings) and personal data (emails, phone numbers, IPs if configured) from logs and events inside the cluster before sending; tests with a corpus of real-looking secrets
- [x] 3.8 (G) First-run health scan right after install (first useful answer in minutes) + on-demand read-only tasks from the UI (fetch logs, describe a workload) via an agent task queue with allow-list and audit
- [x] 3.9 (G) Workload usage history from the agent alone (periodic metrics-server sampling, stored server-side) when Prometheus is missing
- [x] 3.10 (G) Deployment history from GitHub Actions; metrics via Prometheus and OpenTelemetry (OTLP) in addition to metrics-server
- [x] 3.11 (G) Cause-and-effect investigations: one investigation joining a cost spike, a deployment, an incident and a metric change on a shared timeline, instead of separate alerts
- [x] 3.12 (G) "Did the fix work?": compare key signals before / during / after a fix or change, with a clear verdict
- [x] 3.13 (G) AI summary also produces a ready-to-paste team status update (Slack/Teams/email-friendly)
- [~] 3.14 (G) Test lab: scripted reproductions of real Kubernetes failures (OOM, crash loop, bad config, image pull, probe failure, node pressure, DNS) on a test cluster (kind), scoring how often the top-ranked diagnosis is right; tracked over time — *replay lab done (12 scenarios, scored in CI); live kind lab written, not yet run: BLOCKED(Q12)*
- [x] 3.15 (J) Start an investigation manually (cluster, namespace, workload, time window) and from incoming alert webhooks (Prometheus Alertmanager, PagerDuty, Opsgenie) — signed, de-duplicated by alert fingerprint
- [x] 3.16 (J) Close an investigation with the real cause and what fixed it; feed this back to measure and improve top-ranked-cause accuracy (per check, over time, alongside the 3.14 lab score)
- [x] 3.17 (J) Agent management: choose watched namespaces (system namespaces skipped by default), rotate/revoke a cluster's agent token, show agent version + last seen, warn when outdated or silent
- [x] 3.18 (J) Agent auto-discovers in-cluster Prometheus; customers add their own metric queries (validated, read-only, rate-limited)
- [x] 3.19 (J) Custom prices for clusters outside AWS (on-premises, other clouds): per vCPU, per GB memory, per node, Spot discount

### 4. Optimisation
- [x] 4.1 Findings framework: savings/month, risk, effort, steps/commands, dedup key, single ranked list without double counting
- [x] 4.2 Waste detectors: unattached EBS, idle EIPs, unused LBs, old snapshots, stopped instances, previous-gen storage/instances, idle NAT
- [x] 4.3 Right-sizing: EC2, RDS, containers from real usage
- [x] 4.4 Database checks: oversized/idle, extended-support engine versions, Multi-AZ on non-prod
- [x] 4.5 Savings Plans / RI coverage, utilisation, buy recommendations
- [x] 4.6 Network/data-transfer: cross-AZ, internet egress, NAT processing
- [x] 4.7 Workflow found→approved→applied→verified, owner, comments, history; realised savings measured from the bill
- [x] 4.8 Reliability targets (SLOs) per service; recommendations respect them
- [x] 4.9 (A) AWS audit framework: a service-by-service checklist over the whole catalogue where every row ends as *checked & fine*, *saving found*, *needs more data* or *manual review*, with reasons; audit runs, history and a printable audit report
- [x] 4.10 (A) Audit checks — compute: EC2, Auto Scaling, Spot, Lambda, ECS/Fargate, Batch; containers: ECR images and lifecycle policies, EKS (extended support, idle node groups)
- [x] 4.11 (A) Audit checks — storage: S3 lifecycle, old object versions, incomplete multipart uploads, Intelligent-Tiering, Glacier, EBS, EFS, FSx, AWS Backup
- [x] 4.12 (A) Audit checks — databases: RDS/Aurora per engine, DynamoDB capacity mode, ElastiCache, OpenSearch, Redshift, DocumentDB, Neptune, DMS
- [x] 4.13 (A) Audit checks — analytics (Athena, Glue, EMR, QuickSight), AI/ML (SageMaker endpoints/notebooks, Bedrock provisioned throughput, knowledge bases), messaging (SQS, SNS, EventBridge, Step Functions, Kinesis, MSK, MQ)
- [x] 4.14 (A) Audit checks — networking (public IPv4 charges, CloudFront, Route 53, Transit Gateway, VPN, Direct Connect), observability (CloudWatch log retention/ingestion, custom metrics, dashboards, alarms, X-Ray), security (KMS, Secrets Manager, WAF, CloudTrail), developer tools, end-user computing
- [x] 4.15 (A) Non-production resources running 24×7 → schedule suggestions; Graviton and Spot suggestions
- [x] 4.16 (A) Import AWS's own advice (Compute Optimizer, Cost Optimization Hub, Trusted Advisor) and merge with ours by resource + action, no duplicates, sources shown
- [x] 4.17 (A) Purchase-advice pipeline decided in order: instance family/size → Graviton → Spot → Savings Plans / reservations, each step sized on the result of the previous one so recommendations never contradict
- [x] 4.18 (B) Commitments inventory: every Savings Plan, reservation (EC2, RDS, ElastiCache, OpenSearch, Redshift) and negotiated contract (e.g. EDP/private pricing, entered manually) with expiry, utilisation and coverage; expiry alerts
- [x] 4.19 (B) Laddered Savings Plan purchase plan + reservation planning for EC2, RDS, ElastiCache, OpenSearch, Redshift, sized conservatively (on a low percentile of steady usage, after 4.17)
- [x] 4.20 (B) Purchase risk controls (spend limits, max term, upfront options), 7-day preview of planned purchases before anything is bought, advice for under-used reservations (exchange/sell/modify), savings verified after each purchase and month by month
- [x] 4.21 (C) Network cost attribution from VPC Flow Logs (read-only, S3/CloudWatch): who causes NAT, cross-zone, inter-region and internet transfer cost — app, team, workload or IP — with monthly cost; joins K8s pod IPs
- [x] 4.22 (F) Finding confidence (high / medium / low) with written reasons (data coverage, usage history length, volatility)
- [x] 4.23 (F) "Doesn't apply to us" dismissals (with reason and scope, reversible) + per-check noise statistics
- [x] 4.24 (F) Owner routing rules: which team or person owns which finding (by account, tag, virtual tag, service, namespace)
- [x] 4.25 (F) Quick-wins plan: findings sorted into this week / this month / later from savings, effort, risk and confidence
- [x] 4.26 (F) "What you kept": savings proven on the bill after a fix and rechecked monthly (detects regressions)
- [x] 4.27 (F) ROI pack for finance: what the product saved vs its price, verified and estimated numbers kept separate, with the evidence behind each figure (PDF + CSV)
- [x] 4.28 (G) Database and cache advice that improves performance as well as cost (connections, memory pressure, storage I/O, cache hit rate, engine features)
- [x] 4.29 (G) Upgrade advice: old instance generations and extended-support charges (RDS, Aurora, EKS, ElastiCache, OpenSearch and others) with cost of staying vs upgrading

### 5. Governance and reporting
- [x] 5.1 Budgets + forecasts; (D) nested budgets of any depth (organisation → department → team → project); alerts (email, Slack, Teams)
- [x] 5.2 Policies (e.g. owner tag required) + violations
- [x] 5.3 Allocation rules, showback/chargeback, shared-cost split rules
- [x] 5.4 Monthly statements, invoice reconciliation, closed-month locking
- [x] 5.5 Scheduled PDF (reportlab) and email reports
- [x] 5.6 Unit economics: custom metrics ingest, cost per unit
- [x] 5.7 Executive summary page
- [x] 5.8 (D) Early warnings: forecast overspend before month end; same-day spend alerts (from hourly/CUR data where available)
- [x] 5.9 (D) Custom cost report builder (dimensions, filters, grouping, period, chart type; saved and shared)
- [x] 5.10 (D) "Ask in plain words": AI turns a sentence (e.g. "EC2 cost by region for 3 months") into a report-builder query — structured output validated against the schema, shown to the user before running, never raw SQL
- [x] 5.11 (D) One-page monthly leadership report (PDF + email)
- [x] 5.12 (D) FOCUS export; allocation that always adds up to the cent (largest-remainder rounding, invariant tests)

### 6. Integrations
- [x] 6.1 Slack + Microsoft Teams notifications; weekly digest
- [x] 6.2 Jira / ServiceNow tickets; GitHub / GitLab PRs for fixes
- [x] 6.3 Cost impact estimate of planned changes (Terraform plan JSON, K8s manifests); CLI for CI
- [x] 6.4 Outgoing webhooks: HMAC-signed, retries, public HTTPS only (SSRF guard: DNS resolve + private-range block)

### 7. More clouds and services
- [x] 7.1 Azure cost + recommendations (read-only)
- [x] 7.2 Google Cloud cost + recommendations (read-only)
- [x] 7.3 AI/LLM costs incl. Bedrock; usage SDK for customers (Python + TypeScript)
- [x] 7.4 Snowflake, Databricks, Datadog, MongoDB Atlas cost connectors
- [x] 7.5 (H) Explain why AI spend changed (model, team, feature, tokens in/out, price changes); token-waste analyser: repeated prompts, oversized context, expensive model where a cheaper one would do, retries
- [x] 7.6 (H) Bedrock spend per app and team from model invocation logs (read-only)
- [x] 7.7 (H) SDK privacy: never sends prompt or completion text, only counts and fingerprints (salted hashes); enforced by tests
- [x] 7.8 (J) Azure and Google Cloud setup help: exactly which read-only roles to grant (copyable commands), what the last collection saw (scopes, rows, date range, errors), and a "collect now" button

### 8. Enterprise and partners
- [x] 8.1 Org API keys (read/write/admin scopes, hashed, rate-limited); public REST API docs (OpenAPI)
- [x] 8.2 Teams inside an org; verified email domains (DNS TXT)
- [x] 8.3 SCIM 2.0 user provisioning
- [x] 8.4 Temporary elevated access with expiry + audit
- [x] 8.5 Partner mode: manage client orgs, white-label branding, re-billing with markup
- [x] 8.6 Opt-in automatic fixes: separate customer-approved role, approval steps, expiry, spend limits
- [x] 8.7 Read-only MCP server for AI assistants (per-org API key)
- [x] 8.8 (I) Fine-grained permissions beyond the four roles (custom roles from permission sets, scoped to teams/accounts)
- [x] 8.9 (I) Enforce SSO for verified domains; people from a verified domain can join automatically (with default role)
- [x] 8.10 (I) Partner portfolio sorted by what needs attention; client rebill statements as PDF in the partner's branding that never show the partner's cost or margin
- [x] 8.11 (I) Auto clean-up: find idle resources and file approval requests — never act without approval and every guard satisfied; approved reservation changes through the separate opt-in role (8.6)
- [x] 8.12 (J) Write-access banner: when automatic fixes (8.6) are enabled for an organisation, a clear banner on every page with who enabled it and when, plus one-click "turn off" (audited)
- [x] 8.13 (J) Partner sign-up: self-serve or approved by Yugiverse (instance setting, default: approval required); client invitation links that expire; logo upload size limit and format check (PNG/SVG/WebP, SVG sanitised)
- [x] 8.14 (J) MCP connector may also create a new investigation — the only write it allows (scoped key permission, audited)

### 9. Onboarding and usability
- [x] 9.1 Guided setup wizard + next-steps checklist
- [x] 9.2 Plain-English explanations + glossary for finance users
- [x] 9.3 Responsive + accessibility audit (keyboard, contrast, screen reader), light/dark polish
- [x] 9.4 In-app notification centre: per-user inbox (alerts, finished/failed imports, approvals waiting, invitations), unread badge, mark read, per-type preferences; one notification service that also fans out to email/Slack/Teams
- [x] 9.5 (I) Onboarding that shows the first savings quickly, a trial countdown, and a live check that the billing export bucket is readable

### B. Business
- [x] B.1 Plans, entitlements and limits engine (data-driven; prices BLOCKED(Q2)) — *engine done with placeholder plans; real names, limits and prices wait for Q2*
- [x] B.2 Subscriptions: trial, upgrade, downgrade, cancel — provider-agnostic core
- [ ] B.3 Razorpay (INR, GST invoices) — BLOCKED(Q2, Q3)
- [ ] B.4 Stripe (USD) — BLOCKED(Q2, Q3)
- [x] B.5 Signed, de-duplicated payment webhooks (both providers)
- [x] B.6 Self-hosted licence: Ed25519-signed offline key, grace period, free tier without licence — *production public key: Q14*
- [x] B.7 Licence issuing tool (separate CLI; private key from file/env/KMS, never in repo or image) — *file and env built; KMS waits for Q7*
- [x] B.8 Proprietary LICENSE + generated third-party notice (done in 0.1; legal review in Q6)
- [x] B.9 Public website: product, pricing, getting started, security, support — *prices wait for Q2, contact addresses for Q6, hosting for Q7*
- [x] B.10 Draft legal pages: terms, EULA, privacy, DPA, SLA, refund — needs lawyer review (Q6) — *drafted in `legal/drafts`; review and placeholders: Q6*
- [x] B.11 Docs: architecture, installation (SaaS + self-hosted), security, public API, releasing
- [x] B.12 Usage metering per organisation (spend under management, connected accounts, clusters, users, API calls, AI summaries): daily snapshots, feeds plan-limit checks (B.1) and billing (B.2–B.4); visible to the customer
- [x] B.13 Public status page and changelog / release notes page (changelog generated from release notes in the repo; status from uptime checks in D.9) — *changelog done; the status page is Uptime Kuma's own page, set up with D.9*
- [x] B.14 (J) Licence status in the UI for self-hosted: plan, limits and usage, days left, warnings before expiry and during the grace period
- [ ] B.15 (J) Monthly and annual plans; proration on plan changes; downloadable invoices and receipts (GST invoices for India) — BLOCKED(Q2, Q3)
- [x] B.16 (J) Failed payments: retries, reminder emails, grace period, then downgrade to the free tier without deleting data
- [ ] B.17 (J) RBI e-mandate rules for Indian card/UPI auto-renewal: pre-debit notification, customer approval for charges above the limit — BLOCKED(Q3)

### D. Delivery
- [x] D.1 Dockerfile (multi-stage), compose.yaml (web+worker+PG), deploy/compose/local.yaml
- [x] D.2 GitHub Actions CI (backend SQLite+PG, frontend, licences, image smoke test)
- [x] D.3 Release workflow: multi-arch GHCR, SBOM, provenance, cosign keyless
- [x] D.4 Helm chart for in-cluster agent (with 3.1)
- [x] D.5 Kubernetes manifests for the platform (self-hosted on K8s)
- [ ] D.6 Terraform for AWS hosting (VPC, ECS/EKS, RDS PG 17, secrets) — BLOCKED(Q7) for target design
- [x] D.7 Pin GitHub Actions to commit SHAs (Dependabot/Renovate to keep fresh)
- [x] D.8 Backup and restore for self-hosted: `costlens backup` / `costlens restore` commands (pg_dump/pg_restore for PostgreSQL, online copy for SQLite, includes encryption-key reminder), guide covering schedule, off-site copies and a tested restore drill
- [x] D.9 SaaS error tracking and uptime monitoring with self-hosted open-source tools, no paid service by default: Sentry-protocol SDK (MIT) reporting to a self-hosted GlitchTip (MIT), Uptime Kuma (MIT) for uptime checks + alerting; error reporting off unless `COSTLENS_ERROR_TRACKING_DSN` is set; PII scrubbing. Hosting cost for these → ask (Q7)

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
| Q2 | **Plans and prices**: plan names, INR and USD prices, trial length (built as `COSTLENS_TRIAL_DAYS`, 14 for now; SaaS only), limits (cloud spend under management, clusters, users, data retention), annual discount. | B.1, B.3, B.4, B.9 pricing page | open |
| Q3 | **Payment accounts**: Razorpay and Stripe accounts (business KYC as a sole proprietorship), GSTIN for GST invoices, place of supply rules, invoice numbering format. | B.3, B.4 | open |
| Q4 | **Transactional email provider** for SaaS (paid service): e.g. Amazon SES, Postmark, Resend — or SMTP only? Self-hosted will use customer SMTP. | 1.9 (SaaS sending) | open |
| Q5 | **Exchange-rate source** — built with the free **ECB** euro reference rates (≈30 currencies incl. INR, USD; working days, carried over weekends) behind a pluggable provider (`COSTLENS_FX_PROVIDER=ecb\|none`). Confirm ECB is fine, or say if finance needs RBI reference rates (INR) or a paid feed. Fixed monthly finance rates come with 2.17 (manual overrides). | nothing (2.11 done with ECB) | proposed, awaiting confirmation |
| Q6 | **Legal**: business address, contact emails (legal@, support@, security@), governing law/jurisdiction (suggest Tamil Nadu, India), and lawyer review of LICENSE, EULA, terms, privacy (DPDP Act 2023 + GDPR), DPA, SLA targets, refund policy. | B.10, final LICENSE | open |
| Q7 | **SaaS hosting design**: AWS region(s) (suggest ap-south-1 Mumbai + us-east-1), ECS Fargate vs EKS, budget. | D.6 | open |
| Q8 | ~~Docker Desktop was not running in sessions 1 and 2, so the image build and PostgreSQL test run were not verified locally (CI does both). Start Docker if you want them verified next session. **Now also needed for 1.2 (row-level security), which can only be tested on PostgreSQL** — without Docker I can build it but only CI will prove it.~~ Resolved 2026-11-05: Docker running; image, compose and PostgreSQL tests verified. | — | resolved |
| Q10 | **Google OAuth client for the SaaS** (free): create a Google Cloud project for Yugiverse, configure the OAuth consent screen (app name CostLens, logo, privacy/terms links, support email; Google verification needed for external users) and an OAuth client (Web) with redirect `https://<saas domain>/api/v1/auth/callback/google`. Give me the client ID; the secret goes only into the hosting secrets. Self-hosted customers create their own. | SaaS sign-in go-live | open |
| Q11 | **AWS collector principal for the SaaS** (free; part of Q7 hosting): an IAM role in Yugiverse's AWS account that assumes customers' read-only roles. Its ARN goes in `COSTLENS_AWS_PRINCIPAL_ARN` and appears in every customer template, so pick a long-lived name (e.g. `arn:aws:iam::<yugiverse-account>:role/costlens-collector`). | SaaS AWS go-live | open |
| Q12 | **Install kind for the live diagnosis lab** (free, Apache-2.0; `brew install kind`, ~10 MB, plus images pulled when the lab runs: busybox, polinux/stress). I did not install it because downloads need your OK. Then I can run `tools/k8s-lab/run.py` and record real scores. | 3.14 live lab | open |
| Q13 | **Licence for the customer SDKs** (`sdk/python` costlens-ai, `sdk/typescript` @costlens/ai). Customers install these inside their own applications, so an open-source licence (Apache-2.0 or MIT) is usual and makes security reviews easier; they contain no product logic, only usage reporting. Currently marked proprietary like the product. Also: publish to PyPI and npm under which account/org name? | publishing the SDKs (7.3) | open |
| Q14 | **Licence signing key pair** (free): make Yugiverse's production Ed25519 key pair with the issuing tool (B.7, `tools/licence-issuer`) on a machine you control, keep the private key offline (password manager / hardware key, plus a backup), and give me only the **public** key to build into `billing/licence.py` (`PUBLIC_KEYS`). Until then no licence verifies and self-hosted installs run the free tier. | selling self-hosted licences (B.6) | open |
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

### 2026-11-05 — Session 8o: 3.2 Kubernetes cost per cluster, namespace, workload; idle
- **Allocation (`k8s/allocation.py`, hourly job `k8s.allocate_costs` at :03):**
  - Node cost per hour comes from the bill: CUR Usage lines for the node's EC2 instance ID
    (taken from providerID), the last 7 billed days averaged, converted to the org
    currency. Without a bill line, it is estimated from list prices per vCPU and per GiB,
    marked "estimate". Overrides go in `cluster.settings.prices`; custom prices UI is 3.19.
  - Node cost is split into CPU and memory parts in proportion to those rates. Each
    running pod gets its requests' share of allocatable capacity.
  - Init containers count as max(init, sum of main containers).
  - Requests above allocatable are scaled down, never up.
  - The remainder is idle per node, stored with the node's free cores and GiB.
  - Pods roll up to their workload: ReplicaSet→Deployment, Job→CronJob, else the owner
    or the bare pod. Pending pods without a node and finished pods are skipped.
  - Results go to `k8s_costs` (RLS; migration 0021). Each cluster/hour is replaced, so
    the job is idempotent.
  - Clusters silent for more than 2 h are not allocated (no extrapolation).
  - Usage-based views (requests vs actual) come with 3.3.
- **API:** `GET /api/v1/k8s/costs`
  - Breakdowns: `group_by` = cluster | namespace | workload | node (idle).
  - Filters: cluster, namespace, period, display currency.
  - Returns totals, allocated vs idle, idle %, estimated %, a daily series, and average
    requests per item.
- **Demo:** "prod-ap-south-1" (EKS, 6 × m6i.2xlarge) with 7 workloads and an
  over-requesting payments gateway (about 50% idle). A crash-looping nightly ETL with
  BackOff/OOMKilling events is included for 3.4/3.6. 30 days of hourly history.
- **UI:** the sidebar **Kubernetes** entry is now live (`/kubernetes`):
  - Tiles for cluster cost, used by workloads, and idle (with %).
  - An estimate notice, and a daily stacked chart (allocated vs idle, with a table view).
  - A Namespaces / Workloads / Idle-by-node table; clicking a namespace drills into its
    workloads.
  - Linked from Costs; "Manage clusters" goes to Settings.
- **Tests:**
  - 8 backend tests: quantities; hand-computed allocation with a billed node and an
    estimated node; roll-ups; skipped pods; best-effort pods; idempotent re-run;
    overcommit capped; API breakdowns, averages and isolation; silent clusters skipped;
    the job; the demo.
  - 1 frontend test (totals, estimate notice, drill-down).
  - Checked in the browser with the demo.
- Results: SQLite 364 passed / 18 skipped; PostgreSQL 382 passed; agent 7; frontend 65;
  all clean.
- Next: 3.3 requests vs usage (metrics-server/Prometheus) and right-sizing.

### 2026-11-05 — Session 8p: 3.3 requests vs usage, right-sizing (+ 3.9)
- **Agent:** samples metrics-server (`metrics.k8s.io/v1beta1` pods, paginated) every 60 s,
  with the interval set by the server. It sends per-container CPU (cores) and memory
  (GiB) to `POST /api/v1/agent/usage`, which accepts up to 20k samples. If metrics-server
  is missing, usage history pauses for 30 min and nothing else is affected. Prometheus
  and OTLP come in 3.10/3.18.
- **Server:**
  - `k8s_usage` (RLS; migration 0022) keeps hourly aggregates per workload container:
    sample count, sums, per-replica peaks, requests and limits in force, and replicas.
  - Pods are mapped to workloads the same way as in cost allocation.
  - Agent clocks more than 10 min off are clamped to server time.
  - Retention: "Kubernetes usage history", 90 days by default.
- **Right-sizing (`k8s/rightsizing.py`):**
  - Recommended request = observed peak × 1.2, rounded up to 10m CPU / 16 MiB. It never
    goes below peak + headroom.
  - Decrease only when it saves at least 20% and there are 24 or more hours of data.
    Increase when the peak is above the request. "Set requests" when there are none.
  - OOM risk when the memory peak is within 10% of the limit, with a suggested limit.
  - Monthly effect = the workload's allocated cost × its share of the requested-capacity
    change.
  - API: `GET /api/v1/k8s/rightsizing` (risks first, then by saving) and
    `GET /api/v1/k8s/usage` (hourly series for one workload).
- **Demo:** 14 days of usage shaped to show every case:
  - payments-gateway lower (≈ −$186/month)
  - storefront raise
  - nightly-etl memory near its limit (OOM risk)
  - opensearch sized well
  - About $269/month could be freed in total.
- **UI:** a right-sizing card on /kubernetes:
  - Per-replica CPU and memory shown as current → suggested, with the peak.
  - An advice badge; OOM warning with the suggested limit ("Raise memory limit").
  - The monthly effect, and "show all containers". Checked in the browser.
- **Tests:**
  - 6 backend tests: rules; aggregation per workload, container and hour; API savings
    and series; isolation; clock clamp; demo cases.
  - 1 agent test (metrics parsing, namespace filter, pause without metrics-server).
  - 1 frontend test.
- Results: SQLite 369 passed / 18 skipped; PostgreSQL 387 passed; agent 8; frontend 66;
  all clean.
- Next: 3.4 incident investigation (events, metrics, logs, changes → ranked causes).

### 2026-11-05 — Session 8q: 3.4 incident investigation
- **Engine (`k8s/investigate.py`):** gathers evidence for a workload or namespace in a
  time window:
  - pods with container states, restart counts and last exit codes
  - Warning events
  - hosting nodes and their conditions
  - CPU and memory peaks against limits over the last 7 days
  - rollouts (ReplicaSets created in or just before the window)
  - recorded change events mentioning the workload or namespace
- **Checks (10):** OOM, crash loop (explains exit codes 1/2/126/127/137/139/143), image
  pull (spots auth errors), missing ConfigMap/Secret, unschedulable (insufficient
  CPU/memory, taints, affinity, unbound PVC), liveness/readiness probes, node pressure or
  NotReady, volume mount, CPU throttling, DNS.
  - Each check returns a cause with confidence, evidence, fixes (e.g. "raise the memory
    limit to at least 3.90 GiB", `kubectl rollout undo` after a rollout) and how to
    verify the fix.
  - Failures that started right after a change get +5% confidence and an "Started
    after: …" line. A back-off caused by OOM is not double-reported as a crash loop.
  - Logs are explicitly marked "not collected yet" (agent tasks, 3.8).
- **Windows:** end at the cluster's last report, the latest state actually known.
- **Model/API:**
  - `k8s_investigations` (RLS, audited, result not in the audit log; migration 0023).
  - `GET /api/v1/k8s/workloads` lists workloads with quick health
    (failing/warning/ok, with reasons).
  - `POST /api/v1/k8s/investigations/run` is read-only, so it also works in the demo.
  - `POST /api/v1/k8s/investigations` saves (members and up).
  - `GET /api/v1/k8s/investigations` lists; `GET /{id}` shows one; `POST /{id}/refresh`
    re-runs it.
- **UI:**
  - `/kubernetes/investigate`: workloads by health with Investigate buttons, the ranked
    causes (evidence / what to do / how to know it worked), facts, timeline, Save, and
    saved investigations.
  - `/kubernetes/investigations/:id` with "Re-run with fresh evidence".
  - Linked from the Kubernetes page.
  - Checked in the browser on the demo: nightly-etl → "Out of memory", 90%.
- **Tests:**
  - 11 backend tests: OOM with memory evidence; crash loop after a rollout (exit 127,
    rollback, boost); 6 parametrised known failures; node pressure plus throttling;
    healthy workload; workloads/run/save/refresh/roles/demo-read-only/isolation.
  - 1 frontend test.
- Results: SQLite 380 passed / 18 skipped; PostgreSQL 398 passed; agent 8; frontend 67;
  all clean.
- Next: 3.5 optional AI summary (anthropic SDK, fake in tests, redaction), then 3.6
  detection + Slack/Teams notifications.

### 2026-11-05 — Session 8r: 3.5 AI plain-English summary + 3.13 team status update
- **Redaction (`core/redact.py`):** applied to every event message in investigation
  evidence and timelines, and to anything sent to AI.
  - Always removes secrets: private keys, credentials in URLs (scheme and host kept),
    JWTs, AWS/GitHub/Slack/Stripe/Google/Anthropic/OpenAI keys, CostLens agent tokens,
    Bearer/Basic values, `password=`/`token:`/`"secret": "…"` style assignments (quoted
    values with spaces included), and long hex strings.
  - Removes emails and phone numbers by default; IPs only when asked.
  - Tested with a 17-item corpus of real-looking secrets; ordinary Kubernetes messages
    are unchanged.
  - Agent-side use comes with 3.7; stored raw event text is redacted when shown.
- **Template wording:** every investigation now has a plain-English `narrative` and a
  `status_update` ready to paste into Slack, Teams or email. The update has five short
  lines: [Investigating]/[All clear] with workload, cluster and namespace; what we see;
  recent change; likely cause with confidence; next step; done when.
- **Optional AI (`k8s/investigate.reword`):**
  - Runs only when the installation has `COSTLENS_ANTHROPIC_API_KEY` and the
    organisation has opted in (the existing AI setting, now worded for anomalies and
    investigations; off by default).
  - Claude (low effort, refusal fallback) rewords the narrative and the status update
    from redacted facts only.
  - Any failure or refusal keeps the template; the template text is kept alongside.
  - The writer now takes a per-use system prompt.
- **UI:** the investigation page shows an "In plain English" card (marked "Worded by AI…
  secrets and personal data removed first") and a "Team status update" card with Copy.
- **Tests:** 19 redaction tests; narrative/status/redaction in investigations; AI off by
  default, used after opt-in, only ever sees redacted text, and falls back on failure.
  Frontend test extended.
- Results: SQLite 401 passed / 18 skipped; PostgreSQL 419 passed; agent 8; frontend 67;
  all clean.
- Next: 3.6 detection of CrashLoopBackOff/OOMKilled/unhealthy workloads + Slack/Teams
  notifications.

### 2026-11-05 — Session 8s: 3.6 failure detection + Slack/Teams notifications
- **Health (`k8s/health.py`):** workload health shared by the API and the detector.
  "Failing" means CrashLoopBackOff, image pull or config errors. "Warning" means OOMKilled,
  Pending, or 5+ restarts.
- **Detection (job `k8s.detect_health`, every 5 min; reporting clusters only, never the
  demo):**
  - A workload that is failing or was OOM-killed gets one automatic investigation
    (`trigger=auto`, with ranked causes) and a notification. No duplicates while open.
  - When it recovers or disappears, the investigation is marked resolved (with
    `resolved_at`) and a "Resolved" notification goes out.
- **Notification channels (`notification_channels`, RLS, audited with the URL redacted,
  URL encrypted; migration 0024):**
  - Slack incoming webhooks and Teams Workflows / webhook.office.com.
  - Hosts are allow-listed and the SSRF guard runs when saving and again on every send.
  - Payloads: Slack blocks with an "Open in CostLens" button; Teams Adaptive Card 1.4
    with an OpenUrl action. Message text is always redacted.
  - The last delivery result is kept on the channel.
  - API `/api/v1/org/notification-channels` (admins): list (URL shown only as a hint),
    add, enable/disable/rename, delete, send test (502 with the reason on failure).
  - Event list starts with `k8s_incident`; anomalies and the digest come with 6.1/9.4.
- **UI:**
  - Settings → Notifications: add a Slack/Teams channel with setup hints, send a test,
    turn it on or off, remove it; delivery failures shown.
  - The Kubernetes page shows "Open incidents" linking to each investigation.
  - Saved investigations are tagged automatic/resolved.
- **Tests:** webhook validation (allowed hosts, look-alike hosts, http, DNS pointing
  inside); payload shape plus redaction; channel API (roles, hidden URL, encrypted at
  rest, test send success and failure, toggle, delete); detection opens once, notifies
  enabled channels only, resolves with a resolved message; job skips the demo.
  Frontend: channel add + test, open incidents.
- Results: SQLite 406 passed / 18 skipped; PostgreSQL 424 passed; agent 8; frontend 68;
  all clean.
- Next: 3.7 agent-side redaction (reuse `core/redact` rules in the agent), then 3.8.

### 2026-11-05 — Session 8t: 3.7 agent-side redaction
- **Agent:** `costlens_agent/redact.py` is a copy of the server rules (the agent cannot
  import the backend). Kubernetes event messages are redacted inside the cluster before
  sending: secrets always, emails and phone numbers always, IPs when
  `COSTLENS_REDACT_IPS=true` (Helm `costlens.redactIps`). Pod specs were already trimmed
  of env, command, args and annotations (3.1). Logs are not collected yet; they will go
  through the same function when 3.8 lands.
- **Server:** event messages are redacted again on ingest, for older agents or custom
  senders.
- **Guard test:** the backend compares the agent's rule list with the server's (names,
  patterns, flags, replacements) and the output on a sample. A change to one copy fails
  CI until the other matches.
- **Tests:** 7 agent tests (a corpus of real-looking secrets and personal data never
  leaves in event messages; optional IP redaction); backend sync guard; server
  redaction on ingest.
- Results: SQLite 408 passed / 18 skipped; PostgreSQL 426 passed; agent 15; frontend 68;
  helm lint clean.
- Next: 3.8 first-run health scan + on-demand read-only agent tasks (logs, describe).

### 2026-11-05 — Session 8u: 3.8 first-run scan + on-demand read-only agent tasks
- **Task queue (`k8s_agent_tasks`, RLS, audited — who asked for what; migration 0025):**
  - Kinds `logs` and `describe` only.
  - Strict parameters: DNS-safe names; tail ≤ 500 lines; ≤ 24 h back; describe only
    Pod/Deployment/StatefulSet/DaemonSet/Job/CronJob, never Secrets.
  - The namespace must be watched.
  - At most 10 queued per cluster (429); tasks unclaimed for 10 min expire with a clear
    message.
  - Members and up can create tasks; readers can view; the demo is read-only.
  - Agent endpoints: `tasks/claim` (row lock, skip-locked, never handed out twice) and
    `tasks/{id}/result`. Only the owning cluster's agent can complete a task.
  - Results are redacted again on the server and capped at 256 KB (the end is kept).
- **Agent:**
  - Polls every 10 s and re-validates every task itself (allow-list, names, caps,
    watched namespaces).
  - Logs come from `read_namespaced_pod_log` (timestamps, optional `previous`
    container). Describe uses the same safe trimmed subset as snapshots (no env) plus
    the object's events.
  - Output is redacted in the cluster. Refusals and errors are reported back as failed
    tasks.
  - `COSTLENS_ALLOW_LOGS=false` / Helm `costlens.allowLogs: false` removes the
    `pods/log` RBAC permission and refuses log tasks.
- **First-run scan:** after the first full report (on the first events post), once per
  cluster: workloads, pods, nodes, failing and warning counts, plus the likely cause and
  first fix for up to 5 unhealthy workloads. Shown under the cluster in
  Settings → Kubernetes with a link to Investigate.
- **UI:** a "Look closer" panel on investigation results and pages: Describe workload,
  Logs per pod/container, and "before crash" (previous container) when it restarted.
  Polls until done; output with Copy; failures and expiry shown.
- **Found and fixed (important):** the redaction rules could backtrack
  catastrophically on long lines (a 300 KB log line hung). All repetitions are now
  bounded. The key/value rule starts at the keyword and uses possessive quantifiers.
  A test redacts 1 MB of adversarial input per pattern in under 1 s (now ~0.3 s). The
  agent copy is synced.
- **Tests:**
  - Backend: task rules (roles, validation → 422, unwatched namespace, queue limit,
    audit); claim once / result / redaction / size cap / expiry / isolation; first scan
    once; real agent completes a logs task end to end with redaction.
  - Agent (7): capped and redacted logs, describe without env, refusals, logs off,
    results posted.
  - Frontend: fetching logs before a crash through the panel.
- Results: SQLite 420 passed / 18 skipped; PostgreSQL 438 passed; agent 22;
  frontend 69; helm lint clean.
- Next: 3.10 deployment history from GitHub Actions + Prometheus/OTLP metrics.

### 2026-11-05 — Session 8v: 3.10 GitHub Actions deployments + Prometheus/OTLP metrics
- **GitHub (`changes/github.py`, one mapper per event):**
  - `workflow_run` completed for deploy-like workflows (name matches deploy, release,
    rollout, ship, publish, promote, helm, argo, CD, production, staging) becomes a
    deployment change: outcome, branch, short SHA, triggering actor, run number, link.
    Skipped/cancelled runs and test workflows are ignored.
  - `deployment_status` failure/error are now kept too ("… deployment of X to env
    failed"); pending states wait for the outcome.
  - These feed Kubernetes investigations automatically ("Started after: Change: …",
    "Recent change:" in the status update). Tested end to end.
  - Setup text updated to subscribe to "Workflow runs".
- **Prometheus (agent):** `COSTLENS_PROMETHEUS_URL` (Helm `prometheus.url`, optional
  bearer token Secret `prometheus.tokenSecret`) runs two fixed read-only instant queries:
  CPU is the 5-minute rate of `container_cpu_usage_seconds_total` and memory is
  `container_memory_working_set_bytes`, summed by namespace/pod/container, with
  NaN/Inf skipped. It falls back to metrics-server whenever Prometheus fails.
  Auto-discovery and custom queries are 3.18.
- **OTLP (server):** `POST /api/v1/agent/otlp/v1/metrics` with the cluster token.
  - Accepts OTLP/HTTP JSON (Collector `otlphttp` exporter, `encoding: json`) from the
    `kubeletstats` receiver: `container.cpu.usage` (or `.utilization`) and
    `container.memory.working_set`.
  - Namespace/pod/container come from resource or point attributes; int64 string
    values are handled; the newest point per series wins.
  - Feeds the same hourly usage history. Unknown metrics are reported as OTLP partial
    success (rejected count). Protobuf gets 415 with the fix.
  - Setup notes in Settings → Kubernetes.
- **Tests:** GitHub Actions mapping (deploy/test workflows, outcomes, cancelled,
  non-completed) and a deploy showing up in an investigation; failed deployment
  statuses. 3 agent Prometheus tests (parsing and scaling, token, filters, NaN,
  fallback, error status). 2 OTLP tests (parser and endpoint: workload mapping,
  partial success, 415, bad token). Helm lint with Prometheus values.
- Results: SQLite 424 passed / 18 skipped; PostgreSQL 442 passed; agent 25;
  frontend 69; all clean.
- Next: 3.11 cause-and-effect investigations (cost spike + deploy + incident + metric on
  one timeline).

### 2026-11-05 — Session 8w: 3.11 cause-and-effect investigations
- **Engine (`k8s/correlate.py`):** per workload, hourly signals on one timeline:
  allocated cost (converted), CPU and memory per replica (summed over containers),
  warning events, plus matching change events and overlapping cost anomalies. Lookback
  is 48 h before the window.
  - Anchor: the last change before the first warnings (or the last change).
  - Effects: each signal after the anchor is compared with the same clock hours one day
    earlier, so daily cycles are not mistaken for effects; without that history, the 6 h
    before. Only changes ≥ 20% (and above a small absolute floor) are reported, plus
    warnings since the change.
  - Output: a story ("change → memory per replica up 71% … → first warnings"), an
    effects table, hourly series, markers, cost anomalies.
- **Attached** to manual, saved and automatic investigations of a single workload.
- **Demo:** a GitHub Actions "Deploy to production" of nightly-etl 20 h before the
  snapshot. Memory per replica rises ~70% after it, and the OOM kills follow. Cost stays
  flat (requests unchanged). The daily CPU cycle is correctly not flagged.
- **UI:** a "Cause and effect" card on investigations: the story, a before/after table,
  and three small aligned line charts (allocated cost, CPU, memory per replica) with
  dashed change markers and a coloured first-warning marker. They are small multiples
  with one axis each, never dual-axis. Checked in the browser on the demo.
- Results: SQLite 426 passed / 18 skipped; PostgreSQL 444 passed; agent 25;
  frontend 69; all clean.
- Next: 3.12 "Did the fix work?" (before / during / after with a verdict).

### 2026-11-05 — Session 8x: 3.12 "Did the fix work?"
- **Engine (`k8s/fixcheck.py`):** for a workload and a fix time (or a recorded change):
  - Incident start defaults to the first failure warning in the day before the fix.
  - Three periods: before (24 h before the incident), during (incident → fix), after
    (fix → now).
  - Per period: failure events per hour (BackOff, OOMKilling, Unhealthy, Failed,
    FailedScheduling, FailedMount, Evicted), CPU and memory per replica, and allocated
    cost per hour, plus current workload health.
  - Verdicts:
    - "too early" (< 1 h since the fix; says when to check again)
    - "fixed" (no failures since the fix and healthy)
    - "improving" (failures more than halved, not failing)
    - "not fixed"
  - Side effects: memory, CPU or cost ≥ 20% different from before the incident.
- **API:** `POST /api/v1/k8s/fix-check` is read-only (works in the demo).
  `POST /api/v1/k8s/investigations/{id}/fix-check` saves the result on the
  investigation (members and up; single-workload investigations only).
- **UI:** a "Did the fix work?" card on investigation results and pages. Pick a
  recorded change from the timeline or a time, then Check (or Check and save). Shows
  the verdict badge, the before/during/after table and side effects; a saved verdict
  appears when the page opens.
- **Tests:** 4 parametrised verdicts; side effects; API with a recorded change on the
  demo (the deploy did not fix anything: "not fixed"); saved on an investigation;
  namespace-only investigation refused. Frontend test.
- Results: SQLite 432 passed / 18 skipped; PostgreSQL 450 passed; agent 25;
  frontend 70; all clean.
- Next: 3.14 test lab (kind cluster scenarios) — needs a local kind binary, then 3.15.

### 2026-11-05 — Session 8y: 3.14 diagnosis test lab (replay done; live lab written)
- **Replay lab (`k8s/lab.py`):** 12 recorded failure shapes run through the real
  investigation engine in a throwaway in-memory database: OOM kill, crash loop (bad
  flag, exit 2), missing binary (exit 127), missing ConfigMap, image pull (registry
  auth; tag typo), liveness probe, node memory pressure, unschedulable (insufficient
  memory), volume attach, DNS, and a healthy control.
  - Every scenario carries noise (healthy pods, Normal events, an unrelated warning in
    another namespace) and uses a fixed clock.
  - Scores whether the top-ranked cause is right, with the rank when it is not.
  - `costlens lab run [--record FILE] [--min 0.9]` prints the score card, appends a
    JSON line (time, version, accuracy, misses) and exits non-zero below the minimum.
  - `test_k8s_lab.py` fails CI if any scenario stops ranking first.
  - Current: 12/12. These scenarios were written alongside the checks, so this guards
    against regressions rather than measuring real-world accuracy; that comes from the
    live lab and from 3.16 feedback.
- **Live lab (`tools/k8s-lab/`):** README, 7 kind manifests (OOM via `stress`, crash
  loop, missing binary, missing ConfigMap, bad image tag, failing liveness probe,
  unschedulable 512Gi request) with the expected cause on line 1, and `run.py`. The
  runner applies each scenario in its own namespace, waits, signs in to a local
  CostLens, runs investigations, scores, records `mode: live` lines, and cleans up.
  Node pressure and DNS are replay-only (documented why). **Not run yet:** kind is
  not installed (Q12).
- **Also:** the CLI was refactored into one function per command (it had grown past
  the lint limits).
- Results: SQLite 435 passed / 18 skipped; PostgreSQL 453 passed; agent 25;
  frontend 70; all clean.
- Next: 3.15 start investigations manually and from Alertmanager/PagerDuty/Opsgenie
  webhooks.

### 2026-11-05 — Session 8z: 3.15 investigations from alerts + custom windows
- **Alert receivers:** inbound webhooks of kind alertmanager / pagerduty / opsgenie,
  bound to a cluster (`inbound_webhooks.cluster_id`; migration 0026).
  `POST /api/v1/org/webhooks/alerts` returns the URL, the secret (once) and setup text:
  an Alertmanager `webhook_configs` snippet with `send_resolved` and a Bearer
  credential; PagerDuty v3 steps; Opsgenie webhook with an Authorization header.
  PagerDuty generates its own signing secret, so `PUT /api/v1/org/webhooks/{id}/secret`
  stores it.
- **Delivery:** `POST /api/v1/hooks/{alertmanager|pagerduty|opsgenie}/{id}`, registered
  per kind so it can never shadow the GitHub route.
  - Auth: PagerDuty `X-PagerDuty-Signature` v1 HMAC (several signatures accepted, for
    rotation); Alertmanager/Opsgenie a Bearer token or `X-CostLens-Token`; all
    constant-time.
  - Parsers: Alertmanager (fingerprint, labels, summary); PagerDuty incident.* events
    (namespace and workload parsed from the title or service); Opsgenie
    (details + key:value tags; Close/Delete resolve).
- **Mapping and de-duplication:** labels → workload: deployment / statefulset /
  daemonset / cronjob / job_name, or a pod resolved to its owner (pod → ReplicaSet →
  Deployment).
  - A firing alert opens an investigation (`trigger=alert`, `fingerprint`) with ranked
    causes, cause and effect, and alert details (redacted), and notifies channels.
  - The same fingerprint refreshes the open investigation; resolved closes it.
  - The 5-minute detection job does not open a second investigation for a workload
    under an open alert investigation.
- **Custom windows:** investigations accept start/end (both or neither, end after start,
  ≤ 72 h, not in the future; the end is capped at the latest report). The UI has
  "Custom window…" with start/end inputs.
- **UI:**
  - Settings → Kubernetes → "Start investigations from alerts": add a receiver (tool,
    cluster, name), setup instructions with Copy shown once, list with last alert,
    PagerDuty "Set signing secret", Remove.
  - The GitHub section lists only GitHub receivers.
- **Also:** `inbound_webhooks.last_received_at` is now ignored by the audit log (every
  delivery used to create an audit entry).
- **Tests:** signatures (HMAC incl. multiple, Bearer, alt header, missing); parsers for
  all three tools and target mapping incl. pod → owner; Alertmanager end to end (bad
  token, wrong kind path, open, dedup refresh, resolve); PagerDuty with its own secret;
  detection does not duplicate; 5 custom-window cases. GitHub webhook tests still pass.
  Frontend: receiver setup.
- Results: SQLite 445 passed / 18 skipped; PostgreSQL 463 passed; agent 25;
  frontend 71; all clean.
- Next: 3.16 close investigations with the real cause and track accuracy.

### 2026-11-05 — Session 8aa: 3.16 close with the real cause + diagnosis accuracy
- **Close:** `POST /api/v1/k8s/investigations/{id}/close` (members and up; audited) takes
  the real cause (one of 10 stable check names in `CHECK_LABELS`, or "other"), what
  caused it and what fixed it. It records the cause CostLens ranked first and the rank
  of the real cause (none = missed). Migration 0027 adds the outcome columns.
- **Accuracy:** `GET /api/v1/k8s/accuracy` returns closed count, top-1 % and top-3 %,
  missed count, per check precision ("right when ranked first") and recall ("found
  first when it was the real cause"), and top-1 by month. The replay lab score sits
  alongside (computed once and cached for an hour). Organisation-scoped.
- **UI:**
  - Saved investigations have "Close with the real cause" (cause, what caused it, what
    fixed it). Once closed: "CostLens ranked it first / #n / missed it".
  - The Investigate page has a "Diagnosis accuracy" card: closed count, top cause
    right, top 3, lab score, per-cause table.
  - Saved list badges include "closed".
- **Tests:** close rules (viewer refused, unknown cause, rank captured, audit trail);
  accuracy maths on a mixed set (50% top-1, 75% top-3, per-check precision/recall,
  month, isolation). 2 frontend tests.
- Results: SQLite 447 passed / 18 skipped; PostgreSQL 465 passed; agent 25;
  frontend 72; all clean.
- Next: 3.17 agent management (watched namespaces, versions, outdated/silent warnings).

### 2026-11-08 — Session 9a: 3.17 agent management (+ a weekday-dependent bug fix)
- **Watched namespaces:**
  - `PATCH /api/v1/clusters/{id}` takes `include_namespaces` or `exclude_namespaces`
    (DNS-label validated, de-duplicated, ≤ 200) and renames. Include wins.
  - The agent picks it up at its next hello (within 5 min).
  - System namespaces stay excluded by default.
  - Cluster output now includes the reported namespaces and the current
    include/exclude lists.
  - UI: Settings → Kubernetes → Namespaces: "All except the ticked ones" / "Only the
    ticked ones" with a checkbox per namespace.
- **Revoke:** `POST /api/v1/clusters/{id}/revoke` replaces the token hash with one
  nobody holds, so the agent stops at once; health shows "Revoked". "New token" after a
  revoke sets the cluster back to waiting.
- **Silent agents:** job `k8s.check_agents` (every 10 min, not the demo) posts to
  notification channels once when an agent goes silent (> 15 min), with what to check,
  and once when it is back. State is kept in `clusters.silent_since` (migration 0028;
  not in the audit log). Version and last-seen display and the outdated/silent badges
  were already there (3.1).
- **Bug fixed (found because today is a Sunday):** cause and effect used day-over-day
  baselines, so a weekend after a Friday deploy looked like "CPU down 30%".
  Comparisons now use the same hour one week earlier first (8 days of history fetched;
  charts still show 48 h), then one day, then the adjacent hours. A new test re-seeds
  the demo for all seven weekdays and checks the story each time.
- **Tests:** namespaces reach the agent (validation, de-dup, rename); revoke → 401 →
  new token works; silent warned once and back again (Slack messages checked);
  weekday independence. Frontend namespaces picker test.
- Results: SQLite 451 passed / 18 skipped; agent 25; frontend 73; lint, types and
  build clean. **PostgreSQL suite not run this time: Docker was not running**
  (migration 0028 is a single nullable column).
- Note: the folder is now a git repository with your commits; I have not run any git
  commands.
- Next: 3.18 Prometheus auto-discovery + customer metric queries; 3.19 custom prices.

### 2026-11-08 — Session 9b: 3.18 Prometheus auto-discovery + customer metric queries
- **Discovery (agent):** with no `prometheus.url` set, the agent scores services by
  known names (prometheus-operated, kube-prometheus-stack-prometheus, prometheus-k8s,
  prometheus-server, prometheus) or by labels `app.kubernetes.io/name=prometheus` /
  `app=prometheus`.
  - Alertmanager, pushgateway, exporters, adapters and operators are skipped; port 9090
    is preferred.
  - Each candidate is probed with `GET /api/v1/status/buildinfo`. This runs at most
    hourly; the address changes if Prometheus moves and is dropped if it disappears.
  - An operator-set URL is never replaced.
  - The agent reports the usage source, the Prometheus address and whether it was
    discovered; CostLens stores this only when it changes and shows it.
- **Customer queries:** `k8s_metric_queries` and `k8s_metric_points` (RLS; migration
  0029; retention "Custom metric history", 30 days by default).
  - PromQL rules (`k8s/promql.py`, with an identical agent copy guarded by a test):
    single line, printable ASCII, ≤ 500 characters, balanced brackets, closed quotes,
    every range or subquery window valid and ≤ 1 day.
  - Admins can add up to 10 per cluster; on/off, remove; readers list them (latest
    values) and read points (≤ 20 series).
  - The agent gets enabled queries in hello and runs them every 5 min. It checks them
    again itself, uses 10 s Prometheus timeouts, accepts vector or scalar results,
    keeps the top 50 series and drops NaN/Inf.
  - It posts results to `/api/v1/agent/metrics`. Only the owning cluster may write;
    labels are capped at 10 and redacted. Errors are stored as the query's last error
    (redacted).
  - Test before saving: agent task kind `promql` (same validation on both sides);
    the result is a short value/labels table.
- **UI:** Settings → Kubernetes → Metrics per cluster: usage source and Prometheus
  address ("found automatically"); your queries with latest values or last error;
  turn on/off, remove; add with Test (runs once in the cluster).
- **Tests:** agent discovery (ranking, skips, failing probe), discovered Prometheus
  reported in hello, query runs (refusal, scalar/vector, Prometheus error), promql
  task. Backend: 11 PromQL rule cases, agent-copy guard, API/agent flow (roles,
  validation, limit, config, storage with redaction, last error, points, agent info,
  disable, cross-cluster 404, isolation, delete), promql task validation. Frontend
  test for the metrics panel.
- Results: SQLite 465 passed / 18 skipped; agent 29; frontend 74; all clean.
  **PostgreSQL not run: Docker is still not running** (migrations 0028 and 0029 are
  untested on PostgreSQL until it is).
- Next: 3.19 custom prices for clusters outside AWS.

### 2026-11-08 — Session 9c: 3.19 custom prices for clusters outside AWS
- **Prices per cluster** (`cluster.settings.prices`):
  - Fields: currency, per vCPU-hour, per GiB-hour, flat per node-hour, Spot discount %
    (0–95), and `always` (ignore the bill, e.g. negotiated or on-premises rates).
  - `PUT /api/v1/clusters/{id}/prices` (admins; validated ranges and currency code),
    `DELETE` to return to the bill and list prices. Shown on the cluster.
  - Applies from the next hourly allocation; past hours keep their prices.
- **Allocation:**
  - Node cost = (vCPU × rate + GiB × rate + node fee) × (1 − Spot discount on Spot
    nodes), converted from the price currency with the day's rate (or the
    organisation's FX override). It stays in the price currency only if no rate exists.
  - The bill still wins for AWS nodes unless `always` is set.
  - Spot detection now also covers GKE Spot / preemptible and AKS spot pools.
  - Costs from your own prices are labelled `price`, so they are not counted in
    "estimated from list prices".
- **UI:** Settings → Kubernetes → Prices per cluster (currency, per vCPU-hour,
  per GiB-hour, per node-hour, Spot discount, always), Save / Use list prices.
- **Tests:** on-premises node with EUR prices plus node fee and a GKE Spot node with a
  60% discount, converted to USD (hand-computed); prices not counted as estimates;
  `always` overrides the bill; API (roles, validation, set, clear). Frontend test.
- Results: SQLite 468 passed / 18 skipped; agent 29; frontend 75; all clean.
  **PostgreSQL not run: Docker is still not running** (migrations 0028–0029 still to
  check on PostgreSQL; 3.19 has no migration).
- **Section 3 status:** all items done except 3.14's live kind lab (BLOCKED Q12).
- Next: section 4 (Optimisation), starting with 4.1.

### 2026-11-08 — Session 9d: 4.1 findings framework + Savings page
- **Model:** `findings` (RLS, audited for status changes only; migration 0030).
  - Detector, category, a dedup key (unique per organisation), an overlap key (the
    resource the saving comes from), title, description, provider, account, region,
    service, resource.
  - Monthly saving in the organisation's currency, risk and effort (low/medium/high),
    steps, commands, evidence.
  - Status open/dismissed/gone with note and who; first/last seen; resolved time. The
    full workflow arrives with 4.7.
- **Detectors (`findings/registry.py`):** `@detector(name, category, title)` returning
  `FindingIn` items, loaded by module name.
  - `findings.service.run` upserts per detector. Each detector runs in a savepoint, so
    a failing detector is logged and skipped without marking its old findings gone.
  - Findings not seen again become "gone"; they reopen if they return.
  - Dismissed findings stay dismissed (the saving still updates).
- **No double counting:** totals take the largest saving per overlap key, overall and per
  category; the list flags findings that overlap a bigger one.
- **Ranking:** saving × risk factor (1 / 0.7 / 0.4) × effort factor (1 / 0.6 / 0.35).
- **First detector:** `k8s.rightsizing` turns "lower requests" advice (≥ 1/month) into
  findings with the exact `kubectl set resources` command, steps, evidence, and the
  workload as overlap key. The AWS waste detectors are next (4.2).
- **API / jobs:** `GET /api/v1/findings` (status / category filter; summary, ranked
  items, detectors), `PATCH /{id}` (dismiss/reopen with a note; members), `POST /run`.
  Daily job `findings.run_all` (not the demo); the demo runs the real detectors when
  seeded.
- **UI:** the sidebar **Savings** page is live: possible saving per month (overlaps
  counted once, with a note), findings count, by category; filters (category,
  open/dismissed); Check now; ranked list with risk/effort badges, expandable
  description, steps and copyable commands; dismiss with a reason / reopen.
- **Tests:** lifecycle (new, update, gone, comes back, dismissed stays dismissed,
  failing detector), dedup totals, ranking, API on the demo (k8s finding with
  command, demo read-only) and own organisation (dismiss with note, audit, run,
  isolation). Frontend Savings test; shell navigation test updated.
- Results: SQLite 471 passed / 18 skipped; agent 29; frontend 76; all clean.
  **PostgreSQL not run (Docker off)**: migrations 0028–0030 still to verify there.
- Next: 4.2 waste detectors (unattached EBS, idle EIPs, unused load balancers, old
  snapshots, stopped instances, previous-generation, idle NAT).

### 2026-11-08 — Session 9e: 4.2 AWS waste detectors
- **Detectors (`findings/aws.py`), over the inventory and the bill:**
  - Savings use the resource's real cost (last 30 days of CUR lines, scaled to a month,
    converted) when the bill has it; otherwise public list prices (us-east-1, USD,
    converted at the day's rate; skipped if no rate exists).
  - Each finding says which basis it used. Findings carry safe steps and commands;
    CostLens changes nothing itself.
- **The detectors:**

  | Detector | Finds | Risk / effort | Steps |
  |---|---|---|---|
  | `aws.ebs_unattached` | Volumes in "available" state | medium / low | snapshot, then delete |
  | `aws.eip_idle` | Unattached addresses (list price $0.005/h) | low | — |
  | `aws.snapshot_old` | Older than 90 days, source volume gone | medium | list price is an upper bound (snapshots are incremental) |
  | `aws.ec2_stopped` | Stopped ≥ 14 days; saving = attached volumes | medium / medium | AMI, then terminate |
  | `aws.lb_unused` | Billed for hours with no LCU usage in 14 days | — | needs CUR detail, else no claim |
  | `aws.nat_idle` | Billed for hours with no NatGateway-Bytes in 14 days | — | needs CUR detail, else no claim |
  | `aws.previous_generation` | gp2 → gp3 (exact 20%, online); old instance families → successor (~10% of billed cost, flagged as an estimate, with a Nitro/ENA note) | — | — |

  The gp2 → gp3 and instance-family findings use the resource as their overlap key.
- **Tests:** a realistic inventory plus 30 days of bill lines. Billed vs list-price
  savings, the snapshot-before-delete command, in-use and attached resources ignored,
  orphan vs live snapshots, stopped instance = volume cost, LB with/without LCU, idle
  NAT, gp3 maths, m4 → m6i, totals by category; the demo produces real waste findings.
- Results: SQLite 474 passed / 18 skipped; agent 29; frontend 76; all clean.
  **PostgreSQL not run (Docker off).**
- Next: 4.3 right-sizing for EC2 and RDS from real usage (needs CloudWatch metrics:
  new read-only permission and collector).

### 2026-11-08 — PostgreSQL check after Docker restart
- `make test-pg` (PostgreSQL 17.11): **492 passed**. This covers migrations 0028
  (agent silence), 0029 (metric queries, with RLS), 0030 (findings, with RLS) and
  everything from 3.17–4.2 on PostgreSQL. The earlier "PostgreSQL not run" notes for
  sessions 9a–9e are resolved.

### 2026-11-08 — Session 9f: 4.3 EC2 / RDS right-sizing from real usage
- **Permission:** CloudWatch read actions were already in the read-only role
  (GetMetricData, GetMetricStatistics, ListMetrics, DescribeAlarms) and in the
  permission self-check; no template change was needed.
- **Collector (`clouds/aws/metrics.py`):** runs inside each inventory collection, per
  region, for running EC2 and available RDS instances.
  - Metrics: EC2 CPUUtilization plus CWAgent `mem_used_percent` when the agent
    publishes it; RDS CPUUtilization, FreeableMemory and DatabaseConnections.
  - Fetches 14 days of hourly Average/Maximum/Minimum through GetMetricData (batched,
    paginated).
  - Stores one `resource_metrics` row per resource, metric and day: average, highest
    hourly maximum, lowest minimum, 95th percentile of hourly maxima (RLS; migration
    0031; retention "Resource usage (CloudWatch)", 90 days by default).
  - A missing permission or throttling skips metrics only; the inventory is still
    collected.
- **Detectors (`findings/aws_usage.py`):** need ≥ 7 days of data and a billed cost,
  otherwise no claim ("needs more data").
  - `aws.ec2_rightsizing`:
    - Idle: CPU max < 5% (and memory < 20% if measured) → stop/remove (full saving).
    - Downsize one size when CPU max ≤ 40% and memory max ≤ 42.5% (if measured), so
      the halved size still has the peak under 80–85%. Saving = 50% of billed cost.
    - Same-family size ladder that knows m5/c5/r5 start at large and only
      burstable/Graviton families have medium/small. A burstable CPU-credit note and
      a "memory not measured: install the CloudWatch agent" note where relevant.
    - Commands: stop, modify-instance-attribute, start.
  - `aws.rds_rightsizing` (category database):
    - Idle: no connections at all in 7+ days → final snapshot + delete (high risk).
    - Downsize when CPU max ≤ 40% and memory used ≤ 40% of the class memory (table for
      t/m/r/x families; unknown classes skipped). Maintenance-window note.
  - Containers were already covered by `k8s.rightsizing` (3.3 / 4.1).
  - Findings on the same resource share an overlap key with the previous-generation
    finding, so savings are never added twice.
- **Demo:** 14 days of usage for the demo instances. checkout-web-1 peaks at ~24% CPU, so
  it gets "Downsize from m6i.xlarge to m6i.large"; the others are busy. checkout-db has
  usage but no bill line, so correctly no claim.
- **Tests:** size ladder (10 cases), RDS memory table, collector (pagination, daily
  maths), detectors (idle, downsize, busy, high memory, too little history, no smaller
  size, RDS downsize/idle/unknown class), inventory hook with metrics and with
  CloudWatch denied, demo finding.
- Results: SQLite 489 passed / 18 skipped; **PostgreSQL 507 passed**; agent 29;
  frontend 76; all clean.
- Next: 4.4 database checks (extended-support engine versions, Multi-AZ on non-prod,
  oversized/idle databases — the last two partly covered here).

### 2026-11-08 — Session 9g: 4.4 database checks
- **`aws.rds_extended_support`** (category database):
  - Finds RDS MySQL/PostgreSQL majors past the end of standard support: MySQL 5.7
    (29 Feb 2024), MySQL 8.0 (31 Jul 2026), PostgreSQL 11 (29 Feb 2024), 12 (28 Feb 2025),
    13 (28 Feb 2026).
  - Saving: the bill's itemised ExtendedSupport lines when present; otherwise list
    price $0.10 per vCPU-hour in years 1–2 and $0.20 from year 3, × vCPUs by class
    size, × 2 for Multi-AZ, converted to the org currency.
  - Fix: blue/green upgrade to the next supported major (MySQL 8.4, PostgreSQL 16)
    with rehearsal steps.
  - Aurora is skipped (its own schedule; to be covered by the AWS audit 4.9).
  - **To verify:** the dates and rates come from AWS's published Extended Support
    schedule as I know it. Please check them against the current RDS user guide;
    they live in one table (`EXTENDED_SUPPORT`, `RATE_*`) in
    `findings/aws_db.py`.
- **`aws.rds_multi_az_nonprod`:**
  - Finds Multi-AZ databases that look non-production: env/environment/stage/tier tag
    in dev/test/qa/staging/sandbox/uat/preview…, or the name contains one of those
    words.
  - Saving ≈ 50% of the billed cost; low risk; command `--no-multi-az`.
  - Production-tagged and unbilled databases are left alone.
- Over-sized and idle databases: already done in 4.3 (`aws.rds_rightsizing`).
- **Demo:** checkout-db (PostgreSQL 11.22, db.r6g.2xlarge, Multi-AZ) now shows the
  Extended Support finding at list price (~$2,335/month at the year-3 rate).
- **Tests:** version matching (7 cases incl. Aurora), detectors with a fixed clock
  (year-3 Multi-AZ list price, year-1 rate, billed ExtendedSupport line wins,
  current/Aurora skipped, staging tag, dev name, prod and unbilled ignored), demo.
- Results: SQLite 498 passed / 18 skipped; PostgreSQL 516 passed; agent 29;
  frontend 76; all clean.
- Next: 4.5 Savings Plans / RI coverage, utilisation and buy recommendations.

### 2026-11-08 — Session 9h: 4.5 Savings Plans / RIs (IN PROGRESS, stopped at usage limit)
- **Done (backend, lint/types clean, existing suite passes):**
  - `commitment_snapshots` table (RLS; migration 0032).
  - Cost Explorer collector `clouds/aws/commitments.py`: SP and RI utilisation and
    coverage, SP recommendations (Compute and EC2 Instance), RI recommendations for
    RDS / ElastiCache / OpenSearch / Redshift. About 8 calls per connection per day at
    $0.01 each; each part fails independently.
  - Snapshot helper and daily job `commitments.collect_all` (05:50, before the
    detectors, not the demo).
  - Detectors `aws.sp_purchase` (overlap key per connection, so only the best plan
    counts), `aws.ri_purchase`, `aws.commitment_underused` (< 90% used, no claimed
    saving).
  - API `GET /api/v1/commitments`, `POST /api/v1/commitments/refresh` (admins).
  - Demo snapshot (58% coverage, 96% utilisation, Compute/EC2 SP and RDS RI
    recommendations).
- **Left to do:** tests (collector with a fake CE client; detectors; API; demo); the
  Commitments card on the Savings page plus a frontend test; full `make check` +
  `make test-pg`; then tick 4.5.

### 2026-11-08 — Session 9i: 4.5 finished
- **Tests:** collector normalisation (coverage %, unused commitment, recommendations,
  denied reservations recorded as errors without stopping the rest); detectors (both
  SP plan types share one overlap key, so only the best counts; RI title; under-used at
  82% with no claimed saving; total = best SP + RI); API (roles, refresh with the fake
  gateway, latest per connection, isolation); demo.
- **Fix:** the "latest snapshot per connection" join now uses IS NOT DISTINCT FROM, so a
  snapshot without a connection (the demo) is found on SQLite and PostgreSQL.
- **UI:** a Commitments card on the Savings page, per account: SP coverage, SP used, RI
  coverage, RI used, saved last 30 days, snapshot date; Refresh for admins. The
  purchase suggestions appear in the ranked findings list.
- Results: SQLite 502 passed / 18 skipped; PostgreSQL 520 passed; agent 29;
  frontend 76; all clean.
- Next: 4.6 network / data-transfer (cross-AZ, internet egress, NAT processing).

### 2026-11-08 — Session 9j: 4.6 network / data-transfer
- **Classifier (`costs/network.py`):** bill usage types become kinds:
  - NAT processing (`NatGateway-Bytes`), between zones (`DataTransfer-Regional-Bytes`,
    `xAZ`), between regions (`<from>-<to>-AWS-Out/In-Bytes`), internet egress
    (`DataTransfer-Out-Bytes`; CloudFront counted separately as CDN egress), Transit
    Gateway, VPC endpoints.
  - Unrelated "Bytes" usage (e.g. CloudWatch log ingestion) is ignored.
- **API:** `GET /api/v1/costs/network?days=` (7–90) returns totals per kind with share,
  a daily series per kind, and the top 25 payers (resource, service, account, region),
  converted to the display currency.
- **Review findings (category network), no claimed saving:** which traffic could take a
  cheaper path needs VPC Flow Logs (4.21), so these show the monthly cost in the title
  with concrete steps. Only from 30/month.
  - `aws.network_nat`: per NAT gateway; free S3/DynamoDB gateway endpoints with exact
    `create-vpc-endpoint` commands using the NAT's VPC from the inventory, plus
    interface endpoints for ECR/STS/CloudWatch.
  - `aws.network_cross_az`: per account/region; topology-aware routing, same-zone
    replicas and Kafka rack awareness, NLB cross-zone.
  - `aws.network_egress`: per account/region; CloudFront, compression.
  - `aws.network_inter_region`: per account/region; keep chatty services together,
    replicate reads, check cross-region backups and logs.
- **UI:** Costs → Network: total and top kinds with share, the daily stacked chart by
  kind (validated chart component with table view), a "Who pays for it" table, and a
  note about flow logs.
- **Demo:** extra lines for cross-zone, inter-region and EC2 egress (alongside the
  existing NAT and CloudFront lines); the demo shows every kind and the NAT and
  cross-zone reviews.
- **Lint:** E501 is ignored only for `src/costlens/findings/**` (customer-facing prose);
  everything else keeps the 100-character limit.
- **Tests:** classifier (10 cases), API totals, series, top payer and isolation;
  detectors (titles with cost, NAT endpoint commands with the VPC, threshold, no
  savings); demo. 1 frontend test.
- Results: SQLite 514 passed / 18 skipped; PostgreSQL 532 passed; agent 29;
  frontend 77; all clean.
- Next: 4.7 workflow found → approved → applied → verified, owners, comments, history,
  realised savings from the bill.

### 2026-11-08 — Session 9k: 4.7 savings workflow + realised savings
- **Lifecycle (`findings/workflow.py`):** open → approved → applied → verified, or
  dismissed; any can be reopened; "gone" = the detector no longer sees it.
  - Only allowed moves (409 otherwise); approving and manual verification are
    admin-only (403).
  - Detector runs never touch approved/applied/verified findings.
- **History:** `finding_events` (RLS; migration 0033) records status changes (from/to,
  note), owner changes, comments and measurements, with who did it ("CostLens" for
  the automatic ones).
  - API: `PUT /findings/{id}/owner` (organisation members only),
    `POST /findings/{id}/comments`, `GET /findings/{id}/history`.
- **Realised savings from the bill:** for applied findings, the average daily cost in the
  30 days before the change vs. the whole days after it (the change day and today's
  partial day are skipped).
  - AWS resources use CUR resource cost; Kubernetes workloads use allocated cost.
  - Daily job `findings.verify` (07:10, after imports) marks a finding verified once
    ≥ 7 days are measured and ≥ 50% of the expected saving shows up.
  - `POST /findings/{id}/measure` measures on demand.
  - Findings without a resource in the bill (commitments, network reviews) can be
    verified by hand by an admin.
  - The summary adds the realised (verified) total per month and the verified count;
    list filters: active / open / approved / applied / verified / dismissed.
- **UI (Savings page):**
  - Status filter (default "Being worked on").
  - Tile "Realised (verified in the bill)".
  - Status badge per finding; the expanded finding has a workflow panel: actions for
    the status (Approve only for admins), Measure now, owner picker, realised vs.
    expected with days measured, history, and a comment box.
- **Found and fixed:** measurement included today's partial day (skewed averages); it
  now uses whole days only.
- **Tests:** transitions and roles, owner must be a member, comments and history order
  and contents; verification (verified / too early / too small a drop); Kubernetes
  workload measurement; detectors leave applied findings alone; realised summary.
  Frontend workflow test.
- Results: SQLite 521 passed / 18 skipped; PostgreSQL 539 passed; agent 29;
  frontend 77; all clean.
- Next: 4.8 reliability targets (SLOs) per service; recommendations respect them (done, Session 9l).

### 2026-11-08 — Session 9l: 4.8 reliability targets
- Model `ReliabilityTarget` (migration 0034, RLS, audited): name, match, tier
  (critical / standard / best effort), availability %, headroom %, max utilisation after
  a resize, minimum replicas, notes.
  - A match covers one Kubernetes workload, a namespace (one cluster or all), one AWS
    resource, or AWS resources with a tag (case-insensitive).
- Matching (`findings/targets.py`): the most specific target wins (workload, then
  namespace; resource, then tag). Targets only add safety: headroom is never below 20% and
  the utilisation ceiling never above 80%.
- Effects, applied in one place (`targets.apply`) after every detector:
  - Critical tier: no stop or delete suggestions (idle, stop or terminate, delete,
    release), and the risk of other findings goes up one level.
  - Availability ≥ 99.95%: no "turn off Multi-AZ" suggestion.
  - Downsize findings ("size:") are dropped when the peak CPU or memory × 2 exceeds the
    target's ceiling.
  - Kubernetes right-sizing uses the target's headroom per workload. When replicas are
    below the target's minimum, the finding says to add replicas first.
  - Findings that applied a target carry `evidence.reliability` and a sentence naming it.
- API: `GET/POST/PUT/DELETE /api/v1/reliability-targets`. Reading needs read access;
  changes need admin. The match is validated per kind.
- UI: a new Settings → Reliability tab with a list, an add/edit form and delete. It is
  read-only for members and in the demo.
- Tests: `test_reliability.py` (matching, defaults never weakened, filtering rules,
  headroom, API and permissions) and a frontend test for the add flow.
- Checks: `make check` green (SQLite 525 passed / 18 skipped; agent 29; frontend 78).
  `make test-pg` 543 passed.
- Next: 4.9 AWS audit framework (done, Session 9m).

### 2026-11-08 — Session 9m: 4.9 AWS audit framework
- Catalogue (`aws_audit/catalogue.py`): 53 checks across 11 areas.
  - Areas: compute, containers, storage, databases, analytics, AI/ML, messaging,
    networking, observability, security and commitments.
  - Each check names the bill services it covers (matched as case-insensitive parts of the
    service name), the detectors behind it, and the inventory and CloudWatch data it needs.
  - Checks no detector covers yet carry a short "what to review" text; 4.10–4.14 will
    automate them.
  - Every existing detector belongs to exactly one check (tested).
- Outcome per check (`aws_audit/service.py`), first match wins:
  - active findings → *saving* (total counted once per resource);
  - a detector failed → *needs more data*, with the error;
  - no AWS bill → *needs more data*;
  - no spend and no resources → *checked & fine* ("not used");
  - no detector → *manual review*;
  - spend but no inventory at all → *needs more data* (read access);
  - none of the check's resource types in the inventory → *fine*;
  - no CloudWatch usage → *needs more data*;
  - otherwise → *fine*, with counts.
  - Services in the bill that no check covers get their own *manual review* row, so nothing
    is skipped silently.
- Runs and history: models `AuditRun` and `AuditResult` (migration 0035, RLS). Each row
  records `previous_status` when the result changed since the last run, and the summary
  counts the changes.
  - A run triggered from the app runs the detectors first. The weekly job
    `audits.run_all` (Monday 07:30) runs for organisations with an AWS connection and
    reuses the daily findings.
  - The demo seeds one audit.
- API: `GET /api/v1/audits/catalogue`, `GET/POST /api/v1/audits` (running needs member
  rights), `GET /api/v1/audits/{id}`.
- UI: a new page at `/savings/audit`, linked from Savings as "AWS audit".
  - Tiles for savings and each status; the status tiles act as filters.
  - Area and result filters, a run history picker, tables per area with the reason,
    spend and saving, and "was: …" for changed rows.
  - The printable report uses the browser's print. Print styles hide the app's navigation
    and controls.
- Fix on the way: the new models briefly overwrote `models/audit.py` (the audit log). It
  was restored unchanged from the last commit, and the new models live in
  `models/aws_audit.py`. Added `.claude/launch.json` (api and ui dev servers) for preview.
- Checked in the browser on the demo: 54 rows (12 saving, 14 manual, 28 fine,
  US$6,687/month).
- Checks: `make check` green (SQLite 530 passed / 18 skipped; agent 29; frontend 80).
  `make test-pg` 547 passed (before the last catalogue test was added).
- Next: 4.10 audit checks — compute and containers (done, Session 9n).

### 2026-11-08 — Session 9n: 4.10 compute and container audit checks
- New read-only inventory (`inventory/aws_compute.py`), registered with the existing
  collectors:
  - Lambda functions: memory, runtime, architecture, package type.
  - Auto Scaling groups: min/max/desired, instances, On-Demand share, EKS node-group tags.
  - ECS services (Describe takes 10 at a time; tags; launch type and capacity providers).
  - Batch compute environments.
  - ECR repositories: lifecycle policy, image count, untagged bytes, and "stale" bytes
    (tagged images older than 180 days beyond the newest 10).
  - EKS clusters (version, support type) and managed node groups.
  - Each collector is capped at 100 child objects per parent.
  - Permissions: added probes for Lambda, Auto Scaling, ECS, Batch, ECR and EKS. The
    customer policy gains `batch:DescribeComputeEnvironments`; the rest was already there.
- Fix on the way: a single failing collector used to stop *all* vanished resources being
  marked deleted. Now each collector marks only its own resource types (`COLLECTOR_TYPES`)
  and failures are logged. Tested both ways.
- Detectors (`findings/aws_compute.py`):
  - `aws.lambda_arm64`: x86 functions on runtimes that support Arm; 20% of the billed cost.
    Effort is medium when a rebuild is needed (container image, `provided.*`).
  - `aws.spot_nonprod_capacity`: non-production Auto Scaling groups (not EKS-managed) and
    EKS node groups (non-production by their own tags/name or the cluster's) running fully
    On-Demand. Saving is 60% of the members' billed cost (conservative; stated).
  - `aws.idle_asgs` and `aws.eks_idle_nodegroups`: every instance has at least 7 days of
    CloudWatch data and peak CPU below 5%; the saving is the instances' billed cost.
  - `aws.eks_extended_support`: a version past standard support, priced from the bill
    (usage type ExtendedSupport) or the list price (+0.50 USD/hour).
  - `aws.ecr_cleanup`: repositories with no lifecycle policy; untagged plus stale bytes at
    0.10 USD per GB-month, from 1 a month up.
  - EC2 right-sizing now skips instances in Auto Scaling groups; the group replaces them,
    so they are judged per group.
- Audit: checks can now name an *inspection* that lists specific things to review where
  no saving can be estimated reliably:
  - Auto Scaling groups that never scale (min = max, at least 2 instances).
  - Non-production Fargate services without Fargate Spot.
  - Batch environments with minimum vCPUs above 0, or on On-Demand.
  - ECR repositories without lifecycle policies.
  - Catalogue: compute and container rows are automated now (54 checks; new row
    `eks.nodegroups`).
- Demo: a Lambda function and EKS cluster with resource-level bill lines; an EKS node
  group, a staging Fargate service, a warm Batch environment and an ECR repository without
  a policy. Demo audit: 15 saving, 12 manual, 28 fine, 7,383.56 USD/month.
- **Please verify:** the EKS standard-support end dates in
  `findings/aws_compute.py` (`EKS_STANDARD_END`, 1.23–1.33) against the EKS release
  calendar. Versions newer than the table count as "unknown" and are never flagged.
- Tests: `test_aws_compute.py` covers the collectors (paging, ECR maths, "no lifecycle
  policy" error), the support calendar, every detector with negatives, and the audit
  rows.
- Checks: `make check` green (SQLite 540 passed / 18 skipped; agent 29; frontend 80).
  `make test-pg` 558 passed.
- Next: 4.11 audit checks — storage (done, Session 9o).

### 2026-11-08 — Session 9o: 4.11 storage audit checks
- New read-only inventory (`inventory/aws_storage.py`):
  - S3 buckets, per region via `ListBuckets(BucketRegion=…)` (no per-bucket location
    calls; up to 1,000 buckets): versioning, lifecycle rules (transitions, noncurrent
    expiry, multipart abort, expiry), Intelligent-Tiering configurations and tags.
    "Not configured" errors mean none; other errors such as AccessDenied mean unknown, and
    unknown never produces a suggestion.
  - EFS file systems: size per storage class, throughput mode, lifecycle policy.
  - FSx file systems: type, capacity, storage type, deployment, compression.
  - AWS Backup plans with their rules' retention and cold-storage settings.
  - Permission probes added for S3, EFS, FSx and Backup. The customer policy gains
    `backup:GetBackupPlan` (read-only); everything else was already allowed.
- S3 costs per bucket split by class from resource-level bill lines (`bucket_costs`,
  `s3_class`): standard, infrequent, intelligent tiering, archive, requests, retrieval,
  early delete, transfer.
- Detectors (`findings/aws_storage.py`):
  - `aws.s3_intelligent_tiering`: at least 20 a month of Standard storage, requests under
    20% of that, no transitions or Intelligent-Tiering, and configuration known. Saving
    is 20% of Standard (conservative; the real tiers save 40 to 68%; stated).
  - `aws.efs_lifecycle`: no Infrequent Access policy and at least 50 GB in Standard.
    Assumes 30% of the data is cold (stated); priced from the bill share or the list price.
- Audit inspections, with the specific items named:
  - versioned buckets without noncurrent expiry;
  - buckets without a multipart-abort rule;
  - Glacier retrieval and early-deletion charges above 25% of archive storage (from the
    bill);
  - io1/io2 volumes;
  - EFS on provisioned throughput;
  - Multi-AZ FSx outside production, and Lustre without compression;
  - backup rules that never delete, or keep over a year without cold storage.
  - Inspections now receive the audit facts, including bill data, rather than only the
    inventory.
- Catalogue: the storage area now has 10 rows (`s3.lifecycle` split into
  `s3.tiering`, `s3.versions`, `s3.multipart`, `s3.glacier`; new `ebs.iops`). gp2 → gp3
  stays in `ec2.previous_generation`. Total 59 checks.
- Demo: the data lake bucket (account 444…) has resource-level S3 lines, versioning and no
  lifecycle (about 2,057 USD/month on Intelligent-Tiering). Also an EFS file system with
  no IA policy, an io2 volume, and a backup rule that never deletes. Demo audit: 17
  saving, 15 manual, 27 fine, 9,508 USD/month.
- Tests: `test_aws_storage.py` covers usage classes, every collector (region filter,
  paging, disabled rules ignored, unknown vs none), both detectors with negatives, and
  every storage audit row.
- Checks: `make check` green (SQLite 553 passed / 18 skipped; agent 29; frontend 80).
  `make test-pg` 571 passed.
- Next: 4.12 audit checks — databases (done, Session 9p).

### 2026-11-08 — Session 9p: 4.12 database audit checks
- The user asked to keep going through the checklist without stopping after each item.
- New read-only inventory (`inventory/aws_databases.py`):
  - Aurora, DocumentDB and Neptune clusters (one RDS call; member instance ARNs derived).
  - DynamoDB tables: billing mode, table class, provisioned units.
  - ElastiCache clusters.
  - OpenSearch domains (Describe takes 5 at a time).
  - Redshift clusters (paused state).
  - DMS replication instances with their task counts.
  - RDS instances now also record storage type, licence model and cluster.
  - Policy gains `dms:DescribeReplicationInstances` and `dms:DescribeReplicationTasks`;
    six new permission probes.
- `findings/billing.py`: a reusable monthly cost per resource split by usage type.
- Detectors (`findings/aws_databases.py`), using AWS's published price ratios on the
  bill split:
  - `aws.aurora_io_optimized`: saving = I/O − 30% of instances − 1.25 × storage (cluster
    plus its instances).
  - `aws.dynamodb_table_class`: saving = 60% of storage − 25% of reads/writes.
  - `aws.dms_idle`: no running tasks; the instance's billed cost.
  - `aws.elasticache_previous_gen`: m3/m4/r3/r4/t2…; 10% of the billed cost to Graviton.
- Inspections:
  - Enterprise editions with the licence included;
  - non-production Aurora Serverless with a minimum of 2+ ACU;
  - on-demand DynamoDB tables spending 100+ a month on throughput;
  - non-Graviton cache nodes;
  - OpenSearch on gp2, previous-generation instances, or masters outside production;
  - Redshift outside production running around the clock, or retired node types;
  - DocumentDB/Neptune replicas outside production.
- Catalogue: the databases area has 13 rows (Aurora rows now match RDS bill names).
  Total 64 checks.
- Tests: `test_aws_databases.py` covers the collectors, classifiers, exact saving maths
  with negatives, and every database audit row.
- Checks: `make check` green (SQLite 561 passed / 18 skipped). `make test-pg` 579 passed.

### 2026-11-08 — Session 9q: 4.13 analytics, AI/ML and messaging audit checks
- New read-only inventory (`inventory/aws_analytics.py`):
  - Athena workgroups (scan limit); Glue jobs (execution class, version) and crawlers
    (schedule); EMR clusters (state, auto-termination).
  - SageMaker endpoints (variants, instances, serverless) and notebooks (lifecycle
    configuration).
  - Bedrock provisioned throughput (commitment) and knowledge bases (vector store).
  - SQS queues (receive wait time), Step Functions (type), Kinesis streams (mode, shards,
    retention), MSK clusters (brokers) and MQ brokers (deployment).
  - The policy gains 13 read-only actions (Athena, Glue, EMR, SQS, Step Functions,
    Kinesis, MSK, MQ list/describe); SageMaker and Bedrock were already covered. Six new
    permission probes.
- CloudWatch: SageMaker endpoint `Invocations` per variant (two dimensions), keeping the
  busiest variant per hour.
- Detector `aws.sagemaker_idle_endpoints`: in service, not serverless, at least 7 days
  with zero invocations; the endpoint's billed cost.
- Inspections:
  - Athena workgroups with no scan limit;
  - Glue Spark jobs not on Flex (−34%), Glue 0.9/1.0 (10-minute minimum), crawlers more
    than daily;
  - EMR clusters waiting without auto-termination;
  - notebooks without auto-stop;
  - Bedrock throughput with no commitment, and knowledge bases on OpenSearch Serverless
    (minimum capacity even when idle);
  - SQS short polling;
  - Standard Step Functions spending 50+ a month on transitions;
  - Kinesis retention over 24 h, non-production MSK with 3+ brokers, non-production
    Multi-AZ MQ.
  - QuickSight stays manual: reading users would need a broader permission, not asked
    for.
- Catalogue: 11 rows across analytics, AI/ML and messaging; 66 checks in total.
  `aws_audit/**` now has the same long-prose lint exception as `findings/**`.
- Tests: `test_aws_analytics.py` covers the collectors, crawler frequency, two-dimension
  metrics, the idle-endpoint detector and every row.
- Checks: `make check` green (SQLite 570 passed / 18 skipped). `make test-pg` 588 passed.

### 2026-11-08 — Session 9r: 4.14 networking, observability, security, developer tools, end-user computing
- New read-only inventory (`inventory/aws_platform.py`):
  - CloudFront distributions and Route 53 zones (global; listed from us-east-1).
  - VPN connections (tunnel status) and Transit Gateway attachments; Direct Connect
    connections.
  - Log groups (retention, stored bytes); dashboards and alarms.
  - Customer-managed KMS keys (AWS-managed keys skipped: free).
  - Secrets metadata (never values); WAF web ACLs (regional with their attachments, plus
    CloudFront scope); CloudTrail trails; WorkSpaces with their last connection.
  - The policy gains 8 read-only actions (Direct Connect, CloudWatch dashboards, KMS
    DescribeKey, WAF list, CloudTrail DescribeTrails, WorkSpaces describe).
- Fix: inventory now always visits us-east-1, where global services are listed, and marks
  vanished global resources gone after a complete us-east-1 run. Tested.
- `findings/billing.py` gains `usage_type_costs` (monthly cost by usage-type or service
  pattern) for notes that come only from the bill.
- Detectors (`findings/aws_platform.py`):
  - `aws.log_retention`: never-expiring groups with 10+ GB that are 180+ days old. Saving
    = stored GB × share older than 90 days (steady logging assumed; stated) × 0.03 USD.
  - `aws.vpn_down`: both tunnels down; bill or 0.05 USD/hour.
  - `aws.waf_unused`: regional ACL attached to nothing; 5 USD/month.
- Inspections:
  - idle and in-use public IPv4 from the bill;
  - CloudFront PriceClass_All;
  - empty Route 53 zones;
  - Direct Connect not available, unused TGW attachments, TGW attachment spend;
  - log ingestion spend (with the largest groups and the Infrequent Access log class);
  - dashboards beyond the free 3, alarms with no data, custom-metric spend;
  - X-Ray spend;
  - disabled KMS keys and secrets unread for 90 days;
  - duplicate multi-region trails;
  - CloudFront-scope ACLs to verify;
  - always-on WorkSpaces unused for 30 days.
  - Developer tools stays a manual row.
- Catalogue: 76 checks. The "Other" area now also holds developer tools and WorkSpaces;
  uncovered spend counts only the `other:` rows.
- Tests: `test_aws_platform.py` covers the collectors, the global-region visit and
  mark-gone, every detector with negatives, and every row.
- Checks: `make check` green (SQLite 573 passed / 18 skipped). `make test-pg` 591 passed.

### 2026-11-08 — Session 9s: 4.15 non-production schedules, Graviton and Spot
- `aws.nonprod_schedule` (`findings/aws_schedule.py`): resources that look non-production
  (tags or name, as for Multi-AZ) and have no schedule tag.
  - Covers standalone EC2 instances, RDS instances (not in clusters), Aurora clusters
    (cluster plus its instances) and Auto Scaling groups (not EKS).
  - Saving = 64% of the billed cost (12 hours × 5 weekdays). Each finding carries a stop
    command or scheduled group action, and RDS findings note the 7-day auto-restart.
  - Critical reliability targets block it: stop commands are "destructive" (tested).
- `aws.graviton`: running x86 EC2 (t3/m5/m6i/m7i/c5/c6i/r5/r6i…, not Windows, not in an
  Auto Scaling group) at 18% of the billed cost, and RDS (MySQL, MariaDB, PostgreSQL,
  Aurora; m5/m6i/r5/r6i/t3) at 10%, each to the matching Graviton family.
- Spot: groups were done in 4.10. Standalone non-production On-Demand instances are now
  named for review, since only the owner knows whether they are stateless.
- Catalogue rows `nonprod.schedule` and `graviton` (compute); 78 checks.
- The Savings page category filter knows storage, schedules, containers and
  observability.
- Overlaps (e.g. Graviton, a schedule and right-sizing on one instance) count once in
  totals. Making them not contradict each other is 4.17.
- Tests: `test_aws_schedule.py`.
- Checks: `make check` green (SQLite 575 passed / 18 skipped; frontend 80).
  `make test-pg` 593 passed.

### 2026-11-08 — Session 9t: 4.16 import and merge AWS's own advice
- `clouds/aws/advice.py` imports, read-only, under permissions already in the policy:
  - Cost Optimization Hub `ListRecommendations` (us-east-1);
  - Compute Optimizer EC2, EBS and Lambda per billed region (the best option's saving;
    "Optimized"/"Unavailable" skipped; Lambda ARN version dropped);
  - Trusted Advisor (new API, cost pillar, resource level; "$1,234.50" parsed).
  - Everything is normalised to resource + action (rightsize, stop, delete, upgrade,
    graviton, purchase_savings_plan, purchase_reservation).
  - A source that is off (not enrolled, no Business support) is recorded per connection
    in `settings.advice` and skipped. Its earlier advice is kept, and only sources that
    succeeded are replaced.
- Model `ExternalRecommendation` (migration 0036, RLS, unique per org/source/id). Job
  `advice.collect_all` runs daily at 06:20, before the detectors. API:
  `GET /api/v1/findings/advice` (status per source and connection) and
  `POST /api/v1/findings/advice/refresh` (import now, then merge).
- Merge (`findings/advice.py`):
  - Matching uses action groups (stop/delete = remove; rightsize/upgrade = rightsize;
    graviton; purchases) and several spellings of the resource (id, ARN, last segment).
    Purchase advice matches by action alone.
  - A match annotates our finding with `also_recommended_by` (after every run).
  - Otherwise the detector `aws.imported_advice` (registered last; category "imported")
    creates one finding per resource + action, listing every AWS source and taking the
    largest saving converted to the org currency.
  - Duplicates across AWS tools collapse into that one finding.
- UI: Savings rows show "AWS agrees" or "from AWS", and the expanded row lists each source
  and its own estimate. Audit row `aws.advice` added.
- Demo: Compute Optimizer agrees with the checkout-web-1 downsizing, and a Cost
  Optimization Hub Lambda memory change comes in as a new finding.
- Tests: `test_aws_advice.py` covers the parsers, a source being off (advice kept), the
  merge in every direction and the API; a frontend test covers the display.
- Checks: `make check` green (SQLite 579 passed / 18 skipped; frontend 81).
  `make test-pg` 597 passed.

### 2026-11-08 — Session 9u: 4.17 ordered advice pipeline
- `findings/pipeline.py` runs after every detector run, before the AWS-advice
  annotation. Per resource (overlap key), with these steps:
  - **remove** (idle, stopped, unused): replaces every other suggestion for that
    resource.
  - **size**, then **generation / Graviton**, then **Spot**, then **schedule**: these
    stack. Each saving is resized on the cost left after the steps before it, so they add
    up and never exceed the resource's cost.
  - Within one step the largest wins. Graviton replaces an x86 "newer generation" change
    instead of contradicting it.
  - Graviton's title follows the downsized size ("After downsizing, move i-… to Graviton
    m7g.xlarge"); EC2/RDS downsizing now records its `target`.
  - The originals are kept in `evidence.pipeline.original*`, so re-runs and partial runs
    are idempotent (tested). A finding left alone is restored.
- Commitments come last: Savings Plan purchases are shrunk by the compute spend that
  pending EC2 / Auto Scaling / EKS / Lambda changes remove. The covered On-Demand usage is
  derived from the hourly commitment and discount. AWS's figure is kept and shown; if
  nothing steady is left, the purchase is replaced.
- Totals: stacked steps add up per resource; everything else still counts its largest.
  The "overlaps a bigger saving" flag ignores stacked steps.
- UI: Savings rows show "replaced by a better option" with what replaced it, the order to
  apply stacked steps, and for Savings Plans both AWS's and CostLens's hourly sizes.
- Existing expectation updated: a non-production instance's schedule is now sized after
  its Graviton move.
- Tests: `test_pipeline.py` (order, superseding, idempotence, Savings Plan resizing) and a
  frontend test.
- Checks: `make check` green (SQLite 582 passed / 18 skipped; frontend 82).
  `make test-pg` 600 passed.

### 2026-11-08 — Session 9v: 4.18 commitments inventory
- `clouds/aws/commitment_inventory.py`, read-only:
  - Savings Plans (`DescribeSavingsPlans`, active/queued/payment-pending).
  - Reservations for EC2, RDS, ElastiCache, OpenSearch and Redshift in every billed region:
    count, upfront and hourly price, term, payment option, class, start and end (start +
    duration where AWS gives no end).
  - Utilisation over 30 days from two Cost Explorer calls (Savings Plan details by ARN;
    reservations grouped by subscription, matched by lease id).
  - A failing service or region is recorded and skipped. A commitment that disappears
    from a fully read service is marked `retired`.
- Models `Commitment` and `Contract` (migration 0037, RLS; contracts are audited).
  Contracts are negotiated agreements entered by hand: EDP, private pricing,
  marketplace or other; committed spend, currency, discount, term and notes.
  - Contract progress = billed cost in the period (converted), against the time elapsed;
    forecast at the current daily rate, and the shortfall if any.
- Expiry alerts at 90, 30 and 7 days for commitments and contracts, sent once per
  threshold to channels subscribed to the new `commitment_expiring` event. Changing a
  contract's end date resets its reminders.
  - New channels get every event. Settings → Notifications lets each channel pick
    "Kubernetes incidents" and/or "Commitments ending".
- Job: `commitments.collect_all` also refreshes the inventory and sends the alerts.
  API: `GET /api/v1/commitments/inventory`, `POST/PUT/DELETE /api/v1/commitments/contracts`
  (admins); the refresh also refreshes the inventory.
- UI: a new page at `/savings/commitments`, linked from the Commitments card.
  - A table soonest-to-end first, with a days-left badge (red within 30, amber within 90)
    and utilisation (red under 90%).
  - Contracts with a progress meter and forecast shortfall, and an add/delete form for
    admins.
- Demo: a Compute Savings Plan ending in 24 days, an EC2 and an RDS reservation (RDS 71%
  used), and a two-year EDP.
- Tests: `test_commitment_inventory.py` (parsers, utilisation, retirement, alerts once
  per threshold, contract progress and API) and a frontend test; two existing tests
  updated for the new refresh response and default events.
- Checks: `make check` green (SQLite 586 passed / 18 skipped; frontend 83).
  `make test-pg` 604 passed.

### 2026-11-08 — Session 9w: 4.19 laddered purchase plan and reservation planning
- `costs/commitment_plan.py`:
  - **Savings Plans.** Daily On-Demand spend a Compute Savings Plan can cover: EC2
    Box/Dedicated usage (no Spot, no EBS), Fargate and Lambda duration, excluding usage
    already covered by commitments.
    - Steady usage = a chosen low percentile (default 10th) over the window (default 60
      days, from the first bill day; the newest, incomplete day is left out).
    - Minus the compute spend that pending CostLens changes remove (the 4.17 order).
    - The commitment uses AWS's own Compute Savings Plan discount when its recommendation
      exists, otherwise typical published rates by term and payment option (labelled as
      estimates).
    - Bought in rungs (default 4, every 3 months) so expiries are staggered. Plans ending
      inside the horizon get a "replace if still needed" rung.
  - **Reservations** (RDS, ElastiCache, OpenSearch, Redshift): per region and instance
    type, the low percentile of instances running at once, from consumed hours.
    - Resources CostLens suggests removing or resizing are left out.
    - Priced at the observed hourly rate with typical 1-year discounts (×1.6 for 3-year;
      estimates).
    - Split into "now" and "in 3 months" when more than one.
- API: `GET /api/v1/commitments/plan` (percentile, term, payment, rungs, interval, window;
  validated). Nothing is bought: CostLens stays read-only.
- UI: a "Purchase plan" card on the Commitments page with term and caution choices, the
  sizing explained in one paragraph, the ladder table, and the reservation list.
- Fix on the way: days before the bill starts no longer count as zero usage.
- Tests: `test_commitment_plan.py` (percentile, Savings Plan sizing and ladder with an
  expiring plan, reservations with a skipped resource, no-data case and the API) and a
  frontend assertion.
- Checks: `make check` green (SQLite 589 passed / 18 skipped). `make test-pg` 607 passed.

### 2026-11-09 — Session 9x: 4.20 purchase risk controls, preview, verification
- CostLens never buys a commitment: cloud access stays read-only. 4.20 governs purchases
  that people make in the AWS console.
- `PurchasePolicy` (one per org, audited; migration 0038): maximum new commitment spend
  per month (with the policy's currency), longest term, allowed payment options, maximum
  share of steady usage covered (default 80%), and preview days (at least 7).
- `PlannedPurchase` (audited): planned → approved (admin) → purchased (a person records
  the Savings Plan ARN or reservation id after buying) or cancelled.
  - Planning is blocked (422 `policy_violation`, every reason listed) if the plan breaks
    the policy. Approval checks the policy again.
  - "Purchased" is refused before the planned date (`preview_not_over`). Members plan;
    admins approve and record the purchase.
  - New plans notify channels subscribed to `purchase_planned`; Settings → Notifications
    offers it.
- Verification (`costs/purchases.py`): every month (job `purchases.verify`, 2nd at
  07:40, or on demand) the saving is measured from the bill. It is list (On-Demand) cost
  minus effective cost on lines carrying the commitment's id, since the purchase date,
  and is stored next to the estimate and the utilisation. Once per month.
- Under-used commitments: detector `aws.commitment_actions` gives per-commitment advice
  below 90% use.
  - Convertible EC2 reservation → exchange; standard EC2 → modify (size/AZ) or sell on
    the Reserved Instance Marketplace.
  - Savings Plan or other reservations → put usage under it and do not renew.
  - The unused share counts as recoverable only where exchange or sale is possible.
- UI (Commitments page):
  - "Plan this" on each purchase-plan step (planned at least 8 days ahead).
  - A Planned purchases card with approve, record purchase and cancel, plus measured vs
    estimated savings per month.
  - A Purchase rules form (admins edit; others read).
- Fixed on the way: a flaky existing test (`test_clusters`) assumed the random token had
  no "_"; the default-events test now follows the event list.
- Tests: `test_purchases.py` (every policy rule, advice and detector, the full lifecycle
  with permissions and monthly verification) and the frontend flow.
- Checks: `make check` green (SQLite 592 passed / 18 skipped; agent 29; frontend 83).
  `make test-pg` 610 passed.

### 2026-11-09 — Session 9y: 4.21 network cost attribution from VPC Flow Logs
- `costs/flow_logs.py` (read-only):
  - **Finding the logs.** `ec2:DescribeFlowLogs` per billed region finds the S3 and
    CloudWatch Logs destinations (one entry per destination).
  - **Reading them.** A day of records is read from S3 (the standard
    `AWSLogs/<account>/vpcflowlogs/<region>/<y/m/d>/` path, gzip, at most 50 files /
    200 MB) or CloudWatch (`FilterLogEvents`, at most 200k events).
  - **Formats.** Default and custom formats; the header names the fields, and
    `pkt-srcaddr`/`pkt-dstaddr` win when present.
  - **Who owns an address.** The current network interfaces
    (`ec2:DescribeNetworkInterfaces`) name it as an instance (its tags give the team),
    an AWS service interface (its description) or a NAT gateway. Kubernetes pod IPs name
    a workload instead.
  - **Classification.** Seen on a NAT interface with a private address on the other side:
    NAT processing, charged to that address. Private to public: internet transfer.
    Private to private in another zone: cross-zone, charged to the sender on the sender's
    record and to the receiver on theirs (both sides pay). Private to a known address in
    another region: inter-region. Rejected or no-data records count for nothing.
  - **Pricing.** Each category's billed cost over the last 30 days (the network view) is
    shared out by bytes, so it adds up to the bill. Top 200 owners per category; replaced
    on each run.
- The agent now sends `podIP` and `hostNetwork` (unless `redactIps` is on); agent test
  added.
- Model `NetworkAttribution` (migration 0039, RLS). Settings per connection live in
  `settings.flow_logs` (enabled, sources, last run, record count, errors). Job
  `flow_logs.collect_all` runs daily at 04:30 for connections that turned it on.
- API:
  - `GET/PUT /api/v1/connections/{id}/flow-logs`;
  - `POST …/discover` (admins), which also returns the extra read-only IAM statements
    needed (`s3:GetObject/ListBucket` on the log bucket, `logs:FilterLogEvents` on the
    group);
  - `POST …/run`;
  - `GET /api/v1/costs/network/attribution` (rows plus per-team totals).
- UI: a "Flow logs" dialog per connection (find, turn on with these sources, read now,
  policy to add) and a "Who causes it" card on the network page (kind filter, per-team
  line, table).
- Tests: `test_flow_logs.py` (parsing, ownership including pods, every classification
  rule, discovery and policy, S3 path, the full API with permissions and the cost share)
  and a frontend test.

### 2026-11-09 — Session 9z: 4.22 finding confidence
- `Finding.confidence` (high | medium | low; migration 0040, default medium) and
  `evidence.confidence.reasons`, set after every detector run (`findings/confidence.py`).
  The reasons come from:
  - how it is priced: from the bill, or public list prices (medium);
  - usage history: under 7 days low, under 14 days medium; Kubernetes under 72 h low,
    under a week medium;
  - spikiness: peak more than twice the 95th percentile, medium;
  - memory not measured for an EC2 downsize: medium;
  - a stated assumption (schedules, Spot, Intelligent-Tiering, EFS, log retention,
    Graviton, generation moves): medium;
  - imported AWS advice or AWS purchase recommendations: medium.
  - AWS's own tools agreeing lifts a medium to high, unless it rests on an assumption.
- Ranking now multiplies by confidence (1.0 / 0.85 / 0.6).
- UI: a "medium/low confidence" badge, and the reasons in the expanded row.
- Tests: `test_confidence.py` (11 rule cases, the column set by a run, ranking) and a
  frontend assertion. Note: `run(only=[])` means "all detectors"; tests that want only
  the post-steps pass a name that matches nothing.
- Checks (4.21 + 4.22 together): `make check` green (SQLite 608 passed / 18 skipped; agent
  30; frontend 83). `make test-pg` 626 passed.

### 2026-11-09 — Session 9aa: 4.23 "doesn't apply to us" rules and noise statistics
- `Suppression` rules (migration 0041, RLS, audited) have a reason (doesn't apply,
  accepted cost, finding wrong, already planned, other), a note, an optional expiry, and
  a scope:
  - just this finding;
  - this resource for every check;
  - for one check: resources with a tag (case-insensitive key=value), one account, or
    everywhere.
  - Tag, account and everywhere are admin-only (to create and to remove).
- `findings/suppressions.py` runs after every detector run:
  - Open or approved findings a rule covers are dismissed, with a history event naming
    the rule.
  - It restores the rule link on dismissed findings, because detector runs rewrite
    evidence.
  - Removing a rule reopens what it dismissed. A finding another rule still covers stays
    dismissed and is handed over to that rule.
- Noise per check: dismissed vs acted on (approved/applied/verified), the dismissed share,
  reasons, and "noisy" when at least 60% of 5+ decided findings were dismissed.
- API:
  - `POST /api/v1/findings/{id}/dismiss` (reason, scope, tag, note, expiry days);
  - `GET /api/v1/findings/suppressions` (with the count of findings each covers);
  - `DELETE /api/v1/findings/suppressions/{id}`;
  - `GET /api/v1/findings/noise`.
- UI: "Dismiss" opens a small form (why / for what scope, wider scopes shown to admins,
  tag field, note) instead of a prompt. A "Dismissed and noisy checks" card lists rules
  (remove) and per-check noise.
- Tests: `test_suppressions.py` (permissions, tag/resource scopes, surviving re-runs, rule
  counts and noise, removal with hand-over and reopen) and the frontend dismiss flow.
- Checks: `make check` green (SQLite 609 passed / 18 skipped). `make test-pg` 627 passed.

### 2026-11-09 — Session 9ab: 4.24 owner routing rules
- `Finding.team` and `Finding.owner_source` (rule | person); migration 0042. Assigning
  an owner by hand sets `person`, which rules never override; clearing it hands the
  finding back to the rules.
- `OwnerRule` (audited, RLS): name, priority (lowest wins), a match, and a team and/or
  member.
  - Match on account, tag (key=value, case-insensitive, from the inventory), virtual tag
    (from the resource's bill lines in the last 30 days), service (or provider),
    Kubernetes namespace (from the workload id) or finding category.
- `findings/owners.py` runs after every detector run (after confidence and dismissal
  rules). It writes a history event when routing changes, including "no rule matches
  any more".
- API:
  - `GET/POST/PUT/DELETE /api/v1/findings/owner-rules` (admins; validates the key,
    owner and membership, and returns matching findings and savings);
  - the findings list filters by `team` (or `-` for none) and `owner` (`me` / `-`).
- UI: a new Settings → Ownership tab (rules with their reach, add/delete), a "team: …"
  badge on Savings rows, and an Anyone's / Mine / No owner filter.
- Tests: `test_owners.py` (permissions and validation; tag, account, namespace and
  virtual-tag rules; priority with a catch-all; list filters; a person's choice
  surviving rule changes; fall-through on deletion) and a frontend test.
- Checks: `make check` green (SQLite 610 passed / 18 skipped; frontend 84).
  `make test-pg` 628 passed (before two lint-only fixes).

### 2026-11-09 — Session 9ac: 4.25 quick-wins plan
- `findings/plan.py` sorts open and approved findings with a saving above 0 (replaced
  ones are left out), by score:
  - **This week:** low effort, low risk and not low confidence, at most 10; the overflow
    moves to this month.
  - **This month:** up to medium effort and risk, not low confidence.
  - **Later:** high effort or risk, or low confidence.
  - Totals count each resource once, using the same per-resource rule as the Savings
    page.
- API: `GET /api/v1/findings/plan` (optional `team`).
- UI: a "Plan" card at the top of Savings with three columns: total, count and the top 5
  in each.
- Tests: `test_quick_wins.py` (the cap and overflow, low-confidence and high-risk items
  going to later, replaced ones excluded, the total) and a frontend assertion.
- Checks: `make check` green (SQLite 611 passed / 18 skipped). `make test-pg` 629 passed.

### 2026-11-09 — Session 9ad: 4.26 "what you kept"
- `findings/kept.py`: every month (job `findings.recheck_kept`, 3rd at 07:50) each
  verified finding is rechecked against last month's bill.
  - It compares the month's average daily cost of the resource (missing days count as
    zero, e.g. a deleted resource) with the daily cost before the change, recorded at
    verification.
  - It records `kept_monthly` in `measurement.history`, once per month.
  - **Regression:** under half of the confirmed saving kept. The finding reopens with a
    note and a `regressed` history event, and channels subscribed to the new
    `saving_regressed` event are told; Settings → Notifications offers it.
- API: `GET /api/v1/findings/kept` returns kept per month (latest 12, each resource
  counted once), the latest month, the total and recent regressions.
- UI: a "What you kept" card on Savings (latest month, a bar per month, savings that came
  back).
- Tests: `test_kept.py` (kept vs came back, once a month, history event, notification,
  summary) and a frontend assertion.
- Checks: `make check` green (SQLite 612 passed / 18 skipped). `make test-pg` 630 passed.

### 2026-11-09 — Session 9ae: 4.27 ROI pack for finance
- `reports/roi.py` builds the pack for a period (default: since the "using CostLens
  since" date, else the last 12 months).
  - **Verified** (the only money counted towards the return), each line with its
    evidence: confirmed date, cost per day before → after, months counted, monthly and
    total.
    - Verified findings: the rechecked month's kept figure where one exists, otherwise
      the confirmed saving, from the confirmation month on.
    - Savings that came back: only the months they were rechecked as holding.
    - Bought commitments: their monthly measured saving.
  - **Estimated** (applied or approved, not yet measured), listed apart.
  - **Price:** plans and prices are still BLOCKED (Q2), so an admin enters what they pay
    per month and since when (stored in organisation settings). Return = verified ÷
    (price × months); net when the currencies match.
- Downloads: CSV (summary, then one row per verified or estimated line) and PDF.
  - The PDF uses `reportlab` (already declared, BSD-style licence; nothing new added):
    summary table, evidence table, estimates, and "how these numbers are made".
- API: `GET /api/v1/roi`, `/api/v1/roi/pack.csv`, `/api/v1/roi/pack.pdf` (start/end), and
  `GET/PUT /api/v1/roi/settings` (admins).
- UI: a new page at `/savings/roi`, linked from Savings as "ROI pack": four tiles, period
  pickers, PDF/CSV downloads, the price form (admins) and the evidence table.
- Tests: `test_roi.py` (verified vs came-back vs commitments, the return and net, CSV
  lines, PDF bytes and file name, permissions; month spans) and a frontend test.
- Checks: `make check` green (SQLite 614 passed / 18 skipped; frontend 85).
  `make test-pg` 632 passed.

### 2026-11-09 — Session 9af: 4.28 database and cache advice for performance and cost
- More CloudWatch data:
  - RDS: ReadIOPS, WriteIOPS, DiskQueueDepth, SwapUsage.
  - ElastiCache (new): CacheHitRate, DatabaseMemoryUsagePercentage, Evictions,
    EngineCPUUtilization, CurrConnections.
  - The RDS inventory now records provisioned IOPS.
- Detectors (`findings/aws_db_perf.py`):
  - `aws.rds_io1_to_gp3`: io1 vs gp3 at list prices. gp3 sized to the busiest hour + 30%
    (or the current IOPS without data). The included baseline is 3,000 IOPS, or 12,000
    from 400 GB; Multi-AZ doubles both.
  - `aws.elasticache_rightsizing`: 7+ days with memory under 35%, engine CPU under 40%
    and no evictions; one size down, 50% of the billed cost. Treated as a "size" step in
    the advice pipeline.
- Inspections:
  - RDS: 80%+ of the connection limit (estimated from instance memory per engine; RDS
    Proxy or pooling); memory pressure (free memory under 5% or swap over 256 MB);
    storage queue depth over 10; gp2 bursting above its baseline (gp3 includes 3,000
    IOPS); major versions before PostgreSQL 14 / MySQL 8.
  - Caches: full and evicting; hit rate under 80%.
- Catalogue: `rds.performance`, `rds.storage`, `elasticache.sizing`.
- Tests: `test_db_performance.py` (helpers, both detectors with negatives, every RDS note,
  cache notes, audit rows).
- Checks: `make check` green (SQLite 616 passed / 18 skipped). `make test-pg` 634 passed.

### 2026-11-09 — Session 9ag: 4.29 upgrade advice (cost of staying vs upgrading)
- `findings/upgrades.py` adds three detectors to the existing ones (RDS and EKS extended
  support, previous generations, old cache nodes):
  - `aws.aurora_extended_support`: Aurora MySQL 2 (MySQL 5.7 compatible; standard
    support ended 31 Oct 2024) and Aurora PostgreSQL on the RDS calendar (11, 12, 13).
    The charge comes from the bill (ExtendedSupport usage types on the cluster and its
    instances), or is estimated per vCPU-hour of its instances at the RDS rate (0.10,
    then 0.20 in year 3).
  - `aws.elasticache_extended_support`: Redis OSS 4/5 after 31 Jan 2026. From the bill,
    or estimated as the surcharge share of the node's billed cost assuming an 80%
    premium. Suggests Valkey 8.
  - `aws.opensearch_extended_support`: only when the bill shows the charge (rates and
    versions not modelled).
- "Coming up": RDS, Aurora and EKS resources whose standard support ends in the next 180
  days, with an estimated charge where known.
- API: `GET /api/v1/findings/upgrades` returns the cost of staying (sum), each item with
  target, end date and basis (bill or estimate), and what is coming up.
- UI: a new page at `/savings/upgrades`, linked from Savings as "Upgrades".
- Catalogue rows: `aurora.extended_support`, `elasticache.extended_support`,
  `opensearch.extended_support`.
- **Please verify:** the ElastiCache Extended Support premium (80% estimate) and the
  Aurora MySQL 2 end date, in `findings/upgrades.py`. Bill figures always win when
  present.
- Tests: `test_upgrades.py` (Aurora calendar, the list-price and billed paths, the cache
  estimate, the bill-only OpenSearch rule, coming up) and a frontend test.
- Checks: `make check` green (SQLite 618 passed / 18 skipped; frontend 86).
  `make test-pg` 636 passed.
- **Section 4 is complete** (4.1–4.29).

### 2026-11-09 — Session 9ah: 5.1 budgets, nested budgets and alerts
- `Budget` (migration 0043, RLS, audited; alert state is not audited):
  - parent (self-reference, deleting removes the children), name, amount, currency;
  - period (month / quarter / year), metric (effective / billed);
  - filters on the explorer's dimensions (account, service, region, `tag:` / `vtag:`) and
    the other explorer dimensions;
  - thresholds (% of the amount), "alert on forecast", email recipients, owner.
- `costs/budgets.py`:
  - Filters are ANDed from root to leaf, so a child only counts spend its parents also
    count. Values are intersected per dimension; an empty intersection counts nothing.
  - Spend so far comes from the daily rollup, or from line items when tag-style
    dimensions are involved, converted to the budget currency.
  - Forecast = spent + the average of the last 14 days × the days left.
  - Each budget also reports what is allocated to its children and whether they exceed
    it.
  - Alerts: every threshold fires once per period, the actual crossing first, otherwise
    the forecast crossing. They go to channels subscribed to the new `budget_alert`
    event (Slack/Teams) and by email to the recipients (through the `email.send` job).
    Old periods' state is dropped.
- API: `GET/POST/PUT/DELETE /api/v1/budgets` (members create; dimensions, thresholds,
  emails and cycles are validated; editing resets alerts) and `POST /api/v1/budgets/check`
  (admins). Job `budgets.check` runs daily at 08:10.
- UI: `/governance` is now a real page (it was a placeholder), with a Budgets tab.
  - An indented tree with a spent bar, a forecast shadow, a "forecast over" badge and a
    "children exceed it" badge.
  - An add form: name, amount, period, parent, up to 3 filters, email recipients.
- Emails are checked with a simple pattern instead of `pydantic[email]` (no new
  dependency).
- Tests: `test_budgets.py` (periods, nested filters, the excluded-everything case,
  forecast, allocation, alerts once per period with email and channel messages,
  validation, cascade delete) and a frontend test.
- Checks: `make check` green (SQLite 620 passed / 18 skipped; frontend 87).
  `make test-pg` 638 passed.

### 2026-11-09 — Session 9ai: 5.2 governance policies and violations
- Models `Policy` and `PolicyViolation` (migration 0044, RLS; policies are audited;
  violations are unique per policy and resource).
- Policy kinds (`costs/governance.py`):
  - required tags: missing keys, optional allowed values, case-insensitive;
  - allowed regions;
  - denied instance types: glob patterns on EC2, RDS and ElastiCache sizes;
  - Kubernetes workloads whose containers lack CPU and memory requests.
  - Policies can be limited to resource types and have a severity.
- Evaluation keeps violations current: new ones open, fixed ones resolve, a fix undone
  reopens, and exemptions (with a note) stay until lifted. Each violation shows the
  resource's monthly cost. New violations are summarised per policy to channels
  subscribed to the new `policy_violation` event. Job `governance.evaluate` runs daily
  at 08:20.
- API: `GET/POST/PUT/DELETE /api/v1/governance/policies` (admins; parameters validated
  per kind; saving re-evaluates), `POST /api/v1/governance/run`,
  `GET /api/v1/governance/violations` (policy, status, sorted by cost), and
  `POST/DELETE /api/v1/governance/violations/{id}/exempt` (members).
- UI: Governance → Policies (list with severity, open count and cost; an add form per
  kind; an open-violations table with Exempt). Notifications offer "Budget alerts" and
  "Policy violations".
- Tests: `test_governance.py` (all four kinds, permissions and validation, cost, the
  notification, exemption, resolution after a fix) and a frontend test.
- Checks: `make check` green (SQLite 621 passed / 18 skipped; frontend 88).
  `make test-pg` 639 passed.

### 2026-11-09 — Session 9aj: 5.3 allocation rules, showback and chargeback
- `AllocationRule` (migration 0045, RLS, audited) has:
  - the dimension it splits across (e.g. `tag:team`) and filters for the shared costs
    (explorer dimensions);
  - a method: by own cost, evenly, or fixed percentages that must total 100;
  - optional targets, and a position (first match takes a cost; same position goes in
    name order).
- `costs/allocation.py` groups the month by the target dimension plus the rules' filter
  dimensions.
  - Each cost goes to the first matching rule's pool, otherwise to its own value's direct
    cost ("(untagged)" included).
  - Pools are split across the targets; with no own cost, the split is even.
  - Every split uses largest-remainder rounding to whole cents (`split_cents`): the parts
    always add up exactly. A 500-case property test covers it, and this also covers the
    "to the cent" part of 5.12.
- API: `GET/POST/PUT/DELETE /api/v1/allocation/rules` (admins; percentages checked),
  `GET /api/v1/allocation/showback` and `/showback.csv` (chargeback file with one column
  per rule and a total line).
- UI: Governance → Allocation: dimension and month pickers, a table of own / shared /
  total with a total line, CSV download, and rules (add with source filter, split method
  and targets or percentages; delete; each pool's size this month).
- Tests: `test_allocation.py` (the cent-exact property, a showback with a proportional
  and a fixed pool, untagged costs, validation, CSV) and a frontend test.
- Checks: `make check` green (SQLite 623 passed / 18 skipped; frontend 89).
  `make test-pg` 641 passed.

### 2026-11-09 — Session 9ak: 5.4 statements, invoice reconciliation, closed months
- Models `MonthClose` (with the frozen statement) and `Invoice` (manual or read from AWS);
  migration 0046, RLS, audited.
- `costs/statements.py`:
  - **Statement:** billed cost by charge category, account (top 15) and service (top
    15), plus the total.
  - **Reconciliation:** each invoice against the billed cost of its billing account, same
    currency; within 1 unit or 0.5% it matches.
  - **Closing** (past months only) freezes the statement; invoices are still reconciled
    live. Reopening is audited.
  - **Lock:** `replace_billing_period` now records a `skipped_closed` import run and
    leaves a closed month's data untouched, so every importer (CUR, Cost Explorer,
    files, demo) respects the lock without changes.
  - **AWS invoices:** Invoicing `ListInvoiceSummaries` per connection; the policy gains
    `invoicing:ListInvoiceSummaries`.
- API: `GET /api/v1/statements/{month}` and `/statement.csv`; `POST/DELETE …/close`
  (admins); `POST …/invoices` (manual, duplicates refused); `DELETE …/invoices/{id}`;
  `POST …/invoices/fetch` (from AWS, no duplicates).
- UI: Governance → Statements: month picker, three columns and the total, CSV,
  close/reopen with a note, the reconciliation table with matched/mismatch, an add
  invoice form and "Read from AWS".
- Tests: `test_statements.py` (statement figures, manual and AWS invoices, matched vs
  mismatch, close and the import lock, CSV, reopen and re-import, guards) and a frontend
  test.
- Checks: `make check` green (SQLite 624 passed / 18 skipped; frontend 90).
  `make test-pg` 642 passed.

### 2026-11-09 — Session 9al: 5.5 scheduled PDF and email reports
- Emails can carry attachments (base64 in the job payload so it stays JSON-safe); the
  SMTP sender adds them as MIME parts (tested).
- `ReportSchedule` (migration 0047, RLS, audited; send state is not audited):
  - kind: cost summary, savings, budgets, or showback with a dimension;
  - daily, weekly (day of week) or monthly (day 1–28);
  - PDF or CSV, recipients, enabled.
- `reports/scheduled.py` renders sections with `reportlab` (PDF, header row repeated on
  each page) or CSV:
  - cost summary: last month for monthly schedules, month to date otherwise; by
    service, account and charge type;
  - savings: the quick-wins buckets;
  - budgets: status with forecast;
  - showback: last month.
  - Due rule: enabled, has recipients, from 07:00 UTC, once a day at most, on the right
    weekday or day of month.
  - Job `reports.send_due` runs hourly at :05. A failing report records `last_error`
    and the others still go out.
- API: `GET/POST/PUT/DELETE /api/v1/reports/schedules` (members; emails validated),
  `POST …/{id}/send` (now, through the email job), and
  `GET /api/v1/reports/download?kind=&format=` (any report, now).
- UI: Governance → Reports: download buttons per report, a schedule form (kind,
  frequency, day, format, recipients, showback dimension), and a schedule list with
  last sent, last error, send now and delete.
- Tests: `test_reports.py` (attachments, due rules, every kind renders, CSV content,
  send-due once, mail content) and a frontend test.
- Checks: `make check` green (SQLite 627 passed / 18 skipped; frontend 91).
  `make test-pg` 645 passed.

### 2026-11-09 — Session 9am: 5.6 unit economics
- `UnitMetric` (name, unit, description, which costs it carries as explorer filters,
  metric, target per unit) and `UnitMetricValue` (one value per metric per day);
  migration 0048, RLS; metrics are audited.
- Ingest:
  - `POST /api/v1/unit-metrics/{id}/values`: JSON list, up to 5,000; a repeated day
    replaces its value.
  - `POST …/values.csv`: the raw request body as CSV `day,value`, header optional, 2 MB
    at most. No multipart dependency was added; line errors name the line.
- `costs/unit_economics.py`: daily cost through the budget spend helper (same filters
  and currency handling) divided by the day's units.
  - Monthly cost, units and cost per unit.
  - Overall cost per unit over the days that have values.
  - Recent change: the second half of those days vs the first.
- API: `GET/POST/PUT/DELETE /api/v1/unit-metrics` (members), `GET …/{id}/report`
  (default last 90 days, at most 400).
- UI: Governance → Unit costs: metric picker, CSV upload, an add form (name, unit, cost
  filter, target), the headline cost per unit with the recent change and target, and a
  monthly table.
- Tests: `test_unit_metrics.py` (filters, JSON and CSV ingest with a replace, a bad CSV,
  per-day and overall cost per unit, change, months) and a frontend test.
- Checks: `make check` green (SQLite 628 passed / 18 skipped; frontend 92).
  `make test-pg` 646 passed.

### 2026-11-09 — Session 9an: 5.7 executive summary page
- `reports/executive.py` gathers what CostLens already computes:
  - spend this month, forecast and change vs last month;
  - the 5 biggest service changes over 30 days;
  - budgets whose forecast reaches 100%+;
  - savings open (each resource counted once), doable this week (quick-wins plan), and
    kept last month (rechecked on the bill);
  - Savings Plan coverage and utilisation;
  - the first unit metric's cost per unit and trend;
  - open policy violations and this month's anomalies.
- API: `GET /api/v1/roi/executive` (with the ROI routes, as both serve finance and
  leadership).
- UI: a new page at `/executive` and an "Executive" nav item. Eight tiles link to the page
  behind each number, plus a "biggest changes" list.
- Tests: `test_executive.py` (spend, a budget at risk, savings) and a frontend test.
- Checks: `make check` green (SQLite 629 passed / 18 skipped; frontend 93).
  `make test-pg` 647 passed.

### 2026-11-09 — Session 9ao: 5.8 early warnings
- The alert settings gain four fields: forecast warnings (default on, more than 10%
  above last month) and same-day warnings (default on, more than 30% above usual).
- `costs/early_warnings.py`:
  - **Forecast overspend:** the month-end forecast is above last month by more than the
    percentage and by at least the alert minimum; the top growing services over 7 days
    are named. Sent once a month.
  - **Same-day:** whole hours of today from the hourly billing data (CUR) against the
    average of the same hours over the last 7 days; at least 4 hours of data and a
    tenth of the alert minimum. The services that drive it are named. Sent once a day.
  - Both go to channels subscribed to the new `early_warning` event. Job
    `costs.early_warnings` runs hourly at :20.
- API: `GET /api/v1/costs/early-warnings` (current warnings, nothing sent).
- UI: a warning banner at the top of Overview, and two settings lines under Settings →
  General → Alerts.
- Tests: `test_early_warnings.py` (same-day totals and drivers, forecast, once-only
  sending, turning them off), a frontend banner test, and the alert-settings expectation
  in `test_anomalies.py` updated for the new fields.
- Checks: `make check` green (SQLite 630 passed / 18 skipped; frontend 94).
  `make test-pg` 648 passed.

### 2026-11-09 — Session 9ap: 5.9 custom cost report builder
- The cost explorer already covered most of this: dimensions including tags and virtual
  tags, two-level grouping, filters, day/month/total, amortised or billed, and saved
  views shared with the organisation.
- Added:
  - a chart choice (stacked bars, lines, table only);
  - custom dates ("Custom dates" with from/to) next to the last-N-days presets.
  - Both are stored in the saved report's query. The chart setting is never sent to
    the explore API.
- `StackedSpendChart` takes `kind` ("bar" | "line").
- Tests: a new explorer test (custom dates reach the API, the chart choice stays in the
  browser, table-only hides the chart, the saved report keeps dates and chart).
- Checks: `make check` green (SQLite 630 passed / 18 skipped; frontend 95).
  `make test-pg` 648 passed.

### 2026-11-09 — Session 9aq: 5.10 "Ask in plain words"
- `costs/ask.py`: the AI fills in a small, strict JSON object (structured output). The
  object holds: understood, explanation, days or start/end, metric, granularity,
  group_by, filters as dimension/values pairs, and chart.
  - That object becomes an `ExploreQuery` and is validated exactly like a hand-made
    query: known dimensions only, at most two group-bys, dates in order. Anything else
    is refused with `ai_bad_query`.
  - No SQL is produced. Filter values are passed as query parameters.
- What goes to the AI: the question, the latest data date, the dimension names, the top
  50 tag keys, and the service, region and charge-type names from the last 90 days.
  Amounts are never sent.
- `AnthropicWriter.structured()` uses `output_config.format` (json_schema) at low effort
  with the server-side fallback. It returns None on refusal, max_tokens or bad JSON.
- API: `POST /api/v1/costs/ask` returns `{query, chart, explanation}` and does not run
  the query.
  - It needs AI on the server (`ai_disabled`) and the organisation's existing AI switch
    (`ai_explanations`, `ai_off`). The settings text now names this use and what is
    sent.
- UI: an "Ask in plain words" box on the cost explorer. It shows the suggested dates,
  grouping, filters and granularity, and only "Use this report" applies them; Discard
  drops them.
- Tests:
  - `test_ask.py` uses a fake writer: a sentence becomes a query that then runs through
    the explorer; exact dates and tags; a bad dimension, a backwards range, not
    understood and AI failure are refused; the server and organisation switches are
    enforced.
  - Structured-output cases added to the fake-client writer test.
  - Two frontend tests: apply runs only on click, and errors are shown.
- Checks: `make check` green (SQLite 634 passed / 18 skipped; frontend 97).
  `make test-pg` 652 passed.

### 2026-11-09 — Session 9ar: 5.11 one-page monthly leadership report
- `reports/leadership.py` covers the last full month, or any month on request:
  - total and change against the month before;
  - the three services that grew and the three that shrank most;
  - monthly budgets as at month end, and which went over;
  - savings open and savings confirmed on that month's bill;
  - Savings Plans coverage, the first unit cost, anomalies in the month, and open
    policy violations.
  - It also writes three or four plain sentences from those numbers (template wording;
    no AI).
- PDF: a single A4 page (reportlab). The content sits in a `KeepInFrame` in shrink mode,
  so long lists get smaller instead of running onto a second page. The page shows
  whether the month is closed.
- Email: a new scheduled-report kind, "Leadership one-pager". The email body carries the
  sentences and key numbers, with the PDF attached. CSV download works too.
- API: `GET /api/v1/reports/leadership?month=YYYY-MM` returns the data;
  `/api/v1/reports/download?kind=leadership` returns the PDF.
- UI: a "<month> on one page" card on the Executive page (the sentences, Download PDF,
  and a link to set up the monthly email). The kind appears under Governance → Reports.
- Tests: `test_leadership.py`:
  - the numbers, movers, budgets over and sentences;
  - the PDF stays one page with 40 budgets and 12× the sentences;
  - the scheduled email body, attachment name and CSV.
  - The frontend executive and reports tests were extended.

### 2026-11-09 — Session 9as: 5.12 FOCUS export and allocation to the cent
- `costs/focus_export.py` exports a month of line items with FinOps FOCUS 1.2 column
  names:
  - covered: billed, effective and list cost, billing and charge periods, charge and
    pricing category, commitment discount, consumed quantity and unit, provider,
    region, zone, resource, service category and name, SKU, sub-account, tags as JSON;
  - `x_UsageType` and `x_Operation` are extra columns.
  - Amounts are exact, in the billing currency. Columns CostLens does not hold are left
    out rather than guessed.
  - Rows are read in batches. The endpoint writes to a spooled temporary file while the
    session is open, then streams it.
- API: `GET /api/v1/statements/{month}/focus.csv`. UI: a "FOCUS export" button on
  Governance → Statements.
- Allocation to the cent was already in place from 5.3:
  - `split_cents` uses largest-remainder rounding, and showback reports `adds_up`;
  - `test_allocation.py` has the invariant tests: randomised splits always sum exactly
    to the total in whole cents, and zero weights split evenly.
- Tests: `test_focus_export.py` covers:
  - the column order;
  - only the caller's organisation;
  - period timestamps, tags and custom columns;
  - an exact sum equal to the bill, across several batches;
  - an empty month with the header only;
  - a bad month (422).
- Checks (5.11 + 5.12): `make check` green (SQLite 638 passed / 18 skipped; frontend
  97; agent 30). `make test-pg` 656 passed.

### 2026-11-09 — Session 9at: 6.1 anomaly alerts to channels and the weekly digest
- Two new notification events: `cost_anomaly` and `weekly_digest`.
  - The anomaly email step also posts one message to Slack/Teams channels subscribed to
    `cost_anomaly`: the top 10 increases, each with its one-line explanation.
  - Channels saved before this keep their chosen events, so an admin ticks the new ones
    under Settings → Notifications.
- `costs/digest.py` builds the weekly digest:
  - the last 7 days with data against the 7 before;
  - the three services that moved most;
  - unusual increases that week;
  - budgets whose forecast is over 100%;
  - savings waiting.
- Delivery:
  - Job `costs.weekly_digest` runs Monday 08:00.
  - It sends once per ISO week per organisation (state in the organisation's settings)
    and skips demo organisations and organisations with no data.
  - Email to owners and admins is opt-in with the new `weekly_digest_email` alert
    setting (a checkbox under Alerts). Channels choose it per event.
- Fix from 5.8: Settings → Notifications did not list "Early warnings". It is listed
  now, together with the two new events.
- Tests: `test_digest.py` covers:
  - the digest numbers and wording;
  - channel and email delivery, once a week;
  - skipping demo and empty organisations;
  - anomaly alerts reaching channels.
  - The alert-settings expectation was updated.
- Checks: `make check` green (SQLite 641 passed / 18 skipped; frontend 97; agent 30). `make test-pg` 659 passed.

### 2026-11-09 — Session 9au: 6.2 tickets and pull requests from findings
- New models: `WorkIntegration` (kind, name, address, user, encrypted token, settings)
  and `FindingLink` (each ticket or PR opened for a finding). Migration 0049 adds both
  with RLS.
- `work/service.py`:
  - **Jira** (Cloud / Data Center): `POST /rest/api/3/issue` in the chosen project and
    issue type. The description is in Atlassian Document Format, with the label
    `costlens`. Auth: email + API token.
  - **ServiceNow**: `POST /api/now/table/<table>`, default `incident`. Auth: user +
    password/token.
  - **GitHub**:
    - read the base branch SHA;
    - create a branch `costlens/<slug>-<id>`;
    - add one Markdown fix plan at `costlens/fixes/<id>.md`;
    - open a pull request.
  - **GitLab**: one commit API call creates the branch and the plan, then a merge
    request opens (removing the source branch on merge).
  - CostLens never edits infrastructure code. The plan holds the saving, risk, effort
    and confidence, where the resource is, the steps, and the commands to review.
  - Addresses are SSRF-checked when saved and again before each call. Tokens are
    encrypted, shown only as their last 4 characters, and redacted in the audit log.
  - Service errors return 502 `integration_failed`, and the integration records the
    last error.
- API:
  - `GET/POST/PATCH/DELETE /api/v1/org/work-integrations` (changes need admin);
  - `GET/POST /api/v1/findings/{id}/links` (members can open).
  - Opening adds a line to the finding's history.
- UI:
  - Settings → "Tickets & PRs" (admins): fields change with the service.
  - On each saving: links to existing tickets/PRs and "Open ticket / PR" (enabled
    integrations only).
- Tests:
  - `test_work.py` uses one fake that answers like all four services. It covers each
    provider's requests (auth headers, bodies, branch and plan contents, links),
    validation (missing project/repo, bad repo name, internal and plain-http
    addresses), permissions, service failures and turned-off integrations.
  - Two frontend tests (settings form; opening a ticket from a saving).
- Self-hosted Jira/GitLab on a private network is blocked by the SSRF guard on purpose.
  Allowing that for self-hosted installs would need an instance-level allow-list, which
  is a follow-up if customers ask.
- Checks: `make check` green (SQLite 644 passed / 18 skipped; frontend 99; agent 30).
  `make test-pg` 662 passed.

### 2026-11-09 — Session 9av: 6.3 cost impact of planned changes, and a CI command
- `costlens/estimate.py` is pure: no database and no network, so it runs in CI.
  - **Terraform**: reads `terraform show -json plan.out` and gives one line per changed
    resource (create, update, replace, delete) with the monthly cost before and after.
    - Priced resource types: EC2 instance (with root disk), EBS volume, RDS instance
      (storage, multi-AZ), ElastiCache cluster, NAT gateway (hourly part), ALB/NLB
      (hourly part), EKS cluster, EKS node group (desired size, spot).
    - Anything else is listed as "not priced" and left out of the total, never
      guessed.
  - **Kubernetes**: reads YAML or JSON manifests and compares workloads' requested CPU
    and memory before and after: replicas, DaemonSet × nodes, CronJob, Pod and List.
- Prices, best first:
  1. The organisation's own hourly price per instance type from the last 30 days of its
     bill (`costs/unit_prices.py`; EC2 BoxUsage, RDS InstanceUsage, ElastiCache
     NodeUsage; effective cost, so discounts count).
  2. Built-in on-demand list prices (USD, us-east-1). Instances are priced as size units
     × a family rate, with ×0.8 for Graviton, ×1.78 for RDS and ×2 for ElastiCache. This
     matches the published t3/m5/c5/r5 Linux prices.
  - **Please verify** the list-price table from time to time.
- API:
  - `GET /api/v1/estimates/prices` (save it as a file for CI);
  - `POST /api/v1/estimates/terraform` (raw plan JSON, 20 MB at most);
  - `POST /api/v1/estimates/kubernetes`.
  - Results include a Markdown table ready for a PR comment.
- CLI: `costlens estimate terraform plan.json [--prices prices.json] [--format
  markdown|json] [--fail-above 100]`, and `costlens estimate kubernetes new.yaml --before
  old.yaml`. `--fail-above` exits with code 2 so a pipeline can stop on large increases.
  - Example step:
    `terraform show -json plan.out > plan.json && costlens estimate terraform plan.json
    --prices prices.json >> $GITHUB_STEP_SUMMARY`.
  - Calling the API from CI with a key will come with org API keys (8.1).
- `pyyaml==6.0.3` (MIT) is now declared directly. It was already installed through the
  Kubernetes client, so nothing new is downloaded and no notice changes.
- Tests: `test_estimate.py` covers:
  - plan lines, notes and totals, with not-priced resources kept out of the total;
  - bill prices winning over list prices;
  - Kubernetes before/after and bad YAML;
  - the API using the bill's prices, and bad input;
  - the CLI in Markdown and JSON, plus `--fail-above`.
- Checks: `make check` green (SQLite 649 passed / 18 skipped; frontend 99; agent 30).
  `make test-pg` 667 passed.

### 2026-11-09 — Session 9aw: 6.4 outgoing webhooks
- Models `OutgoingWebhook` (encrypted URL and secret, events, on/off, last result) and
  `WebhookDelivery` (payload, status, attempts, next attempt). Migration 0050 adds both
  with RLS.
- `core/webhooks_out.py`:
  - **Queueing**: every `notify()` event (the same list as Slack/Teams) is also queued
    for each enabled webhook subscribed to it. Text passes through the secret redactor.
  - **Delivery**: job `webhooks.deliver_due` runs every minute and sends what is due,
    up to 200 a run.
  - **Signature**: `X-CostLens-Signature: t=<unix>,v1=<HMAC-SHA256 hex of
    "<t>.<body>">`, plus `X-CostLens-Event` and `X-CostLens-Delivery` (for
    de-duplication). `verify()` shows what a receiver does, with a 5-minute window.
  - **SSRF guard**: public HTTPS only, DNS resolved and private ranges blocked, both
    when saving and before every attempt. Redirects are not followed, so a 3xx counts
    as a failure.
  - **Retries**: after 1 min, 5 min, 30 min, 2 h and 6 h; failed after 6 attempts.
- API under `/api/v1/org/outgoing-webhooks` (admins):
  - CRUD; the secret is shown only on create and on `rotate-secret`;
  - `test` sends a ping straight away;
  - `deliveries` lists the last 50;
  - `deliveries/{id}/retry`.
- UI: a "Webhooks" card under Settings → Notifications: add (secret shown once), events
  per webhook, send test, new secret, turn off, remove, recent deliveries.
- Tests: `test_webhooks_out.py` covers:
  - sign/verify (wrong secret, changed body, too old, garbage);
  - unsafe addresses refused;
  - the secret shown once and the signed ping;
  - only subscribed events queued, with secrets redacted;
  - the back-off schedule, 302 not followed, success on the third try;
  - failure after the last attempt, then manual retry and already-sent;
  - secret rotation, and nothing queued while turned off.
  - Plus a frontend settings test.
- Section 6 is complete.
- Checks: `make check` green (SQLite 651 passed / 18 skipped; frontend 100; agent 30).
  `make test-pg` 669 passed.

### 2026-11-09 — Session 9ax: 7.1 Azure costs and Azure Advisor (read-only)
- **Connection**: a service principal (tenant ID, client ID, client secret) and a scope
  (subscription, billing account or management group).
  - Saving checks that it can sign in.
  - The secret goes in the new encrypted `cloud_connections.secret` column, is redacted
    in the audit log and is never returned.
  - Migration 0051 adds that column and widens `account_id` to 64 characters on
    connections and external recommendations. Azure subscription IDs are GUIDs: SQLite
    did not mind, PostgreSQL did.
  - Roles: Cost Management Reader on the scope, Reader on the subscriptions (Advisor),
    and optionally Storage Blob Data Reader on the export container.
- `clouds/azure/client.py`:
  - client-credentials sign-in with cached tokens;
  - only `login.microsoftonline.com`, `management.azure.com` and
    `<account>.blob.core.windows.net` (the account name is validated) are ever called;
  - 429 is retried, honouring Retry-After.
- `clouds/azure/costs.py`:
  - **Cost Management Query API**: daily cost by subscription and service, as actual
    (billed) and amortised (effective). Reservation purchases (billed with no amortised
    cost) count as Purchase / Committed; negative amounts count as Credit.
  - **FOCUS export**: when a storage account and container are set, it reads the newest
    export run for each month (CSV, CSV.gz or Parquet) through the existing FOCUS
    parser. That gives region, resource, tags and commitments. The Query import is then
    skipped, so nothing is counted twice.
  - Errors are saved on the connection. A 401/403 marks it as error.
- `clouds/azure/advisor.py`:
  - Azure Advisor cost recommendations per subscription become `ExternalRecommendation`
    rows (source `azure_advisor`), with action rightsize, purchase, delete or other.
  - The monthly saving comes from `savingsAmount`, or `annualSavingsAmount` / 12.
  - The imported-advice detector now makes Azure findings (provider `azure`, "check
    the Azure console").
- Jobs: `costs.import_azure`. The six-hourly import scheduler and `advice.collect_all`
  include Azure connections, as does `POST /findings/advice/refresh`. Advice status
  lists the Azure source only when an Azure connection exists.
- API: `POST /api/v1/connections/azure`. "Collect now" works for Azure. Connections
  carry an `azure` block (scope, IDs, export; never the secret).
- UI: "Connect Azure" in Settings → Cloud accounts, with the optional FOCUS export
  section. The AWS-only buttons (permissions, flow logs) are hidden for Azure rows.
- Tests: `test_azure.py`, with a fake answering like Microsoft sign-in, Cost
  Management, Advisor and Blob storage, covers:
  - actual + amortised merge, purchases, throttling retry, token caching;
  - the newest FOCUS run winning, and a month with no export;
  - refusing other hosts, bad storage names and sign-in errors;
  - connect validation, secret never returned, import plus Advisor end to end;
  - Azure advice becoming an Azure finding.
  - Plus a frontend connect test.
- Checks: `make check` green (SQLite 657 passed / 18 skipped; frontend 101; agent 30).
  `make test-pg` 675 passed.

### 2026-11-09 — Session 9ay: 7.2 Google Cloud costs and Recommender (read-only)
- **Connection**: a service account key (JSON) plus the BigQuery billing export table
  (`project.dataset.table`), an optional project to run queries in, and whether the
  table is the detailed export.
  - The key is checked (type, email, private key, project) and must sign in before
    saving.
  - It is stored in the encrypted `secret` column, enlarged to 8000 characters in 0051
    for key files, and never returned.
  - Roles: BigQuery Data Viewer on the dataset, BigQuery Job User on the query project,
    Recommender Viewer.
- `clouds/gcp/client.py`: an RS256-signed JWT is exchanged for an access token (PyJWT +
  cryptography, both already dependencies). Only `oauth2`, `bigquery` and `recommender`
  on googleapis.com are called.
- `clouds/gcp/costs.py`: one parameterised query per invoice month.
  - The table name is validated by a strict pattern; month and dates are query
    parameters.
  - Rows are grouped by day, project, service, SKU, region, zone, resource (detailed
    export only), cost type, currency and labels.
  - Credits are added in, so cost matches the invoice. Commitment SKUs count as
    Purchase / Committed, tax as Tax, adjustments as Adjustment, and negative usage as
    Credit. Labels become tags; quantity and unit are kept.
  - The importer waits for the job and follows pages. BigQuery bills the customer's
    project for bytes scanned, and the setup text says so.
- `clouds/gcp/recommender.py`: asks only where the last 30 days of the bill show spend
  (at most 50 project/location pairs).
  - Zonal: idle VMs, machine type, idle disks. Regional: idle addresses, commitments.
  - Only COST impacts are kept; the monthly saving comes from the cost projection over
    its duration.
  - A 404 (recommender not in that location) is ignored. On permission errors the
    previous recommendations are kept.
  - Results become `gcp_recommender` external recommendations, then Google Cloud
    findings.
- Jobs, API and UI:
  - `costs.import_gcp`. The scheduler, `advice.collect_all` and
    `/findings/advice/refresh` include Google Cloud.
  - `POST /api/v1/connections/gcp`. Collect now works.
  - Settings → Cloud accounts → "Connect Google Cloud" (key file upload, table, query
    project, detailed checkbox).
- Tests: `test_gcp.py`, with a fake Google that verifies the signed JWT with a real RSA
  key, covers:
  - key checks;
  - the export query (parameters, waiting for the job, paging, row mapping, the
    SQL-injection guard);
  - connect validation, sign-in failure, key never returned;
  - import plus Recommender end to end (locations from the bill, savings, keeping
    results after a permission error).
  - Plus a frontend connect test.
- Checks: `make check` green (SQLite 661 passed / 18 skipped; frontend 102; agent 30).
  `make test-pg` 679 passed. The first test-pg run straight after `make check` reported
  a failure I could not capture; two reruns passed. Watch for a flaky test.

### 2026-11-09 — Session 9az: 7.3 AI/LLM spend and the usage SDKs, with 7.7 SDK privacy
- **Cloud bills**: AI services (Bedrock, SageMaker, Azure OpenAI / Cognitive Services /
  Foundry Models, Vertex AI, Gemini) are picked out of the cost data that is already
  imported.
- **SDK ingest**:
  - Write-only `clai_` keys (hashed, shown once, revocable; model `AiUsageKey`).
  - `POST /api/v1/ingest/ai-usage` takes batches of up to 1000 `AiUsageEvent`s:
    provider, model, app, team, feature, environment, token counts (input, output,
    cache read, cache write), optional reported cost, salted fingerprint, retry, error,
    latency.
  - Migration 0052 adds both tables with RLS.
- **Pricing**: a reported cost wins. Otherwise a built-in price list in USD per million
  tokens (`costs/ai_usage.py PRICES`, longest prefix match, Bedrock prefixes stripped);
  cache reads default to 10% of input and cache writes to 125%.
  - Organisations can override prices (`PUT /api/v1/ai/prices/{model}`). Unknown
    models are counted as unpriced, not guessed.
  - **Please verify** the price list, especially the OpenAI rows. The Claude rows come
    from Anthropic's current price table.
- **Summary**: `GET /api/v1/ai/summary` returns bill services plus SDK totals (cost,
  requests, tokens, cache, retries, unpriced) by model, app, team and feature.
- **UI**: Costs → "AI spend": bill services, SDK totals, four breakdowns, and SDK key
  management for admins.
- **SDKs**, no third-party dependencies, both tested in `make check`:
  - `sdk/python` (`costlens-ai`, stdlib only, Python ≥ 3.10) and `sdk/typescript`
    (`@costlens/ai`, Node ≥ 22.18, run as TypeScript with no build step; type-checked
    with the frontend's tsc).
  - `record()`, plus `from_anthropic` / `fromAnthropic` and `from_openai` /
    `fromOpenAI` helpers that read the responses' usage blocks.
  - Batching, retry on network/5xx (up to 3 times), dropping rejected batches, and a
    bounded buffer. They never throw into the caller.
- **7.7 privacy**:
  - The prompt is used only to make an HMAC-SHA256 fingerprint with a salt that never
    leaves the customer's process. Both SDKs give the same fingerprint, and a test
    pins the value.
  - Names are cleaned to identifier characters, so they cannot hold sentences.
  - The server schema forbids unknown fields, spaces in names and non-hex fingerprints.
  - Tests:
    - each SDK proves the prompt's words never appear in the payload;
    - the backend proves a `prompt` field, sentence-like names and bad fingerprints are
      rejected (422);
    - an end-to-end test drives the real Python SDK into the API and checks none of
      the prompt's words are stored.
- **Open question Q13**: the SDKs' licence (open source is usual for client SDKs) and
  the PyPI/npm names. They are marked proprietary until decided.
- Tests: `test_ai_usage.py` covers pricing, privacy, keys, the SDK end to end, the
  summary, overrides, revocation and clamped timestamps. Also 5 Python SDK tests,
  5 TypeScript SDK tests and 2 frontend tests.
- Checks: `make check` green (SQLite 664 passed / 18 skipped; agent 30; SDKs 5 + 5;
  frontend 104). `make test-pg` 682 passed.

### 2026-11-09 — Session 9ba: 7.4 Snowflake, Databricks, Datadog and MongoDB Atlas
- `connectors/saas.py`: one framework for four read-only connectors. Each turns a
  billing month into cost lines; a shared importer replaces that month per connection.
  - Credentials are a JSON object in the encrypted connection `secret`, never returned.
  - Non-secret settings are whitelisted per connector.
  - Hosts are fixed (Atlas, Datadog sites) or must match the vendor's domain pattern
    (`<account>.snowflakecomputing.com`, `*.cloud.databricks.com` /
    `*.azuredatabricks.net` / `*.gcp.databricks.com`).
  - **Snowflake**: SQL API with key-pair JWT (RS256, public-key fingerprint in `iss`)
    reading `ORGANIZATION_USAGE.USAGE_IN_CURRENCY_DAILY`, with bound parameters. It
    waits for async statements and reads every partition. Optional warehouse and role.
  - **Databricks**: SQL Statement Execution API reading `system.billing.usage` × the
    matching `system.billing.list_prices` row. It waits for the statement, reads
    chunks, and keeps workspace, SKU, product, cluster/warehouse/job, tags and DBUs.
    List price only: negotiated discounts are not in system tables, as the UI says.
  - **Datadog**: `usage/historical_cost` (`estimated_cost` for the current month),
    total charge per product per organisation. Monthly figures are dated on the 1st.
  - **MongoDB Atlas**: invoices API with digest auth: closed invoices for the month and
    the one before, plus the pending invoice. Daily line items per SKU and cluster are
    de-duplicated across invoices and kept within the month.
- API: `POST /api/v1/connections/saas` (provider, name, settings, secret). The
  connection is checked by reading this month's cost before saving. Collect now and
  the six-hourly scheduler use the new `costs.import_saas` job. A 401/403 during import
  marks the connection as error.
- UI: Settings → Cloud accounts → "Connect a service"; the fields change with the
  service and each one shows the access it needs.
- Lint: `connectors/**` and `tests/test_connectors.py` allow long lines (SQL and vendor
  JSON). Pyright now also sees `sdk/python/src` for the SDK end-to-end test, so no
  ignore comment is needed.
- Tests: `test_connectors.py`, with one fake answering like all four vendors (the
  Snowflake JWT is verified with a real RSA key), covers:
  - async statements and partitions, chunks, monthly products, invoice de-duplication;
  - host and site guards;
  - connect validation, failed check, secrets never returned;
  - import end to end, and error status on 403.
  - Plus a frontend connect test.
- Checks: `make check` green (SQLite 669 passed / 18 skipped; agent 30; SDKs 5 + 5;
  frontend 105). `make test-pg` 687 passed.

### 2026-11-09 — Session 9bb: 7.5 why AI spend changed, and the token-waste analyser
- `costs/ai_insights.py explain()` compares the last N days with the N before, from SDK
  events.
  - The change is split by model, team, feature and app (top 5 each).
  - For each model it is split into a **volume effect** (token change × old price per
    token) and a **price/mix effect** (the rest: a price list change, or a different
    mix of input, output and cached tokens). The two always add up.
  - It also writes plain sentences. API: `GET /api/v1/ai/changes?days=7`.
- `waste()` covers the last 30 days, scaled to a month, keeping items worth at least
  $5 a month:
  - **repeated prompts**: the same fingerprint at least 20 times with no cache reads;
    the saving is 90% of the repeats' input cost;
  - **oversized context**: at least 20k input tokens and at least 50× the output; half
    their cost is shown as a rough guide;
  - **expensive model for small jobs**: Fable → Opus 5.5, Opus → Sonnet 5.5,
    gpt-4.1 → mini, gpt-4o → mini, on requests under 2k tokens in and 300 out; priced
    at the cheaper model, with "check quality on a sample";
  - **retries and errors**: their full cost.
  - API: `GET /api/v1/ai/waste`.
- **Savings**: new detector `ai.token_waste` (category "ai", shown as "AI usage") makes
  findings with steps. It has a catalogue entry under AI/ML, so the "every detector in
  exactly one check" rule still holds. The "imported" category label now reads "From
  your cloud", since Azure and Google Cloud advice arrive there too.
- **UI**: the AI spend page gains "What changed this week" and "Wasted tokens".
- Tests: `test_ai_insights.py` covers:
  - the volume and price effects adding up to the change, the drivers and sentences;
  - all four waste kinds with expected savings, and a cheap model not flagged;
  - findings from the detector;
  - the API.
  - The frontend AI test was extended.
- Checks: `make check` green (SQLite 672 passed / 18 skipped; agent 30; SDKs 5 + 5;
  frontend 105). `make test-pg` 690 passed.

### 2026-11-09 — Session 9bc: 7.6 Bedrock spend per app and team from invocation logs
- `clouds/aws/bedrock_logs.py` reads Bedrock model invocation logs (JSON lines,
  gzipped) from the S3 bucket and prefix the customer names, through the existing
  read-only role. It reads the last 3 days by the date folders in the keys, and each
  run replaces those days, so it is safe to repeat.
- **Privacy**: from each record it keeps only time, model, input/output/cache token
  counts, error flag and attribution. Prompt and response text, if the customer's
  logging includes them, are discarded as each record is parsed, and never stored or
  logged. The setup dialog recommends turning off text delivery in Bedrock logging.
  A test puts a prompt and an answer in the logs and checks none of their words are
  stored.
- **Attribution**, first match wins:
  1. `requestMetadata` app/application, team and feature;
  2. the customer's rules ("role contains X → app, team");
  3. the caller's IAM role name.
- **Cost** comes from the price list. Bedrock region prefixes (`us.`, `eu.`, `apac.`,
  `global.` …), `anthropic.` and inference-profile ARNs are handled. Rows land in
  `ai_usage_events` with `source = bedrock_logs:<connection>` (new column, migration
  0052), so the AI spend page and the waste analyser cover Bedrock too.
- **Permissions**: the role templates (policy, CloudFormation, Terraform) gain an
  optional `ReadBedrockInvocationLogs` statement: GetObject/ListBucket on that bucket's
  `<prefix>/AWSLogs/*` only.
- **API**: `PATCH /api/v1/connections/{id}` accepts `bedrock_log_bucket`,
  `bedrock_log_prefix` and `bedrock_rules` (AWS connections only; an empty bucket
  turns it off). `POST /api/v1/connections/{id}/bedrock-logs/import` reads now.
  Connections show `bedrock_logs` (bucket, prefix, last read, events, error). The daily
  job `costs.import_bedrock_logs` runs at 05:40.
- **UI**: a "Bedrock logs" dialog on active AWS connections (bucket, prefix, role
  rules, "Save and read now").
- Tests: `test_bedrock_logs.py` covers:
  - counts and attribution only, never text; the three attribution rules; pricing;
    the date window;
  - re-runs replacing the same days;
  - access errors recorded;
  - the policy statement and the settings API (bucket validation, rule cleaning).
  - Plus a frontend dialog test.
- **Lesson**: the PostgreSQL test database is kept between runs while the head
  revision ID stays the same. Changing a migration in place (as with 0051 and 0052 this
  session, both still uncommitted) therefore needs `make pg-stop` first. That explains
  the unexplained test-pg failure noted under 7.2.
- Checks: `make check` green (SQLite 676 passed / 18 skipped; agent 30; SDKs 5 + 5;
  frontend 106). `make test-pg` 694 passed on a fresh database.

### 2026-11-09 — Session 9bd: 7.8 Azure and Google Cloud setup help
- `clouds/setup_help.py` gives the exact read-only roles, with copyable commands filled
  in with the customer's values. Values are shell-quoted, and anything outside a safe
  character set is dropped.
  - **Azure**: `az ad sp create-for-rbac` with Cost Management Reader on the scope;
    Reader on the subscription for Advisor; Storage Blob Data Reader on the export
    container only, when a FOCUS export is used.
  - **Google Cloud**: a service account; BigQuery Data Viewer on the billing dataset
    (`bq add-iam-policy-binding`); BigQuery Job User on the query project (it can run
    queries, billed to that project, but not change data); Compute Recommender Viewer
    and Project CUD Recommender Viewer on each project; key creation.
  - **Please verify** the role ID `roles/recommender.projectCudViewer` (commitment
    recommendations) against Google's current role list.
- API:
  - `GET /api/v1/connections/setup-help/{azure|gcp}` gives help before connecting.
  - `GET /api/v1/connections/{id}/setup-help` adds the projects from the bill (Google
    Cloud) and **what the last collection saw**: scope, last success, last error, rows
    and date range of the imported data, the last 6 import runs (month, status, rows,
    error) and the recommendation import (count, errors).
  - "Collect now" already works for both clouds (7.1 / 7.2).
- UI:
  - "Which roles to grant (commands)" panels in the Connect Azure and Connect Google
    Cloud dialogs.
  - A "Setup and last collection" dialog on Azure and Google Cloud rows, with the
    roles, a copy button and the last collection.
- Fix: the webhook test used a fixed clock (10:00 today). Its deliveries are queued at
  the real time, so it failed once real time passed 10:00. It now uses the real clock.
- Tests: `test_setup_help.py` covers:
  - Azure commands with scope and container;
  - Google Cloud dataset, job user and projects;
  - unsafe values dropped;
  - the API before and after connecting, including the date range, runs and error.
  - Plus a frontend dialog test.
- Section 7 is complete.
- Checks: `make check` green (SQLite 680 passed / 18 skipped; agent 30; SDKs 5 + 5;
  frontend 107). `make test-pg` 698 passed.

### 2026-11-09 — Session 9be: 8.1 organisation API keys and public API docs
- Model `OrgApiKey` (migration 0053, RLS): name, scope (read / write / admin), SHA-256
  hash, last 6 characters, creator, optional expiry, last used, revoked. Shown once at
  creation (`clk_…`).
- `accounts/api_keys.py` and `auth/web.py`:
  - With no session cookie, `Authorization: Bearer clk_…` is resolved with a
    constant-time hash compare.
  - The key acts for its organisation with the **lower** of its scope (read → viewer,
    write → member, admin → admin; never owner) and its creator's current role.
  - It stops working when revoked, expired, or when the creator leaves or is
    deactivated.
  - Audit events show "API key <name> (…hint) for <creator email>" with actor type
    `api_key`. A stand-in session object (never stored) keeps the request code
    unchanged.
- **Blocked for keys**: sign-in and sessions, `/me`, creating or switching
  organisations, invitations, members, SSO, exports, deleting the organisation, and
  managing API keys.
- **Rate limits**: each key gets its own per-minute "user" and "write" buckets in the
  existing limiter (memory or PostgreSQL), so one script cannot use up a person's
  limit. CSRF does not apply: there is no cookie.
- **API docs**: `/docs` and `/openapi.json` now open with an authentication, limits and
  errors guide, and declare a bearer `apiKey` security scheme.
- API: `GET/POST/DELETE /api/v1/org/api-keys` (admins). UI: Settings → "API keys"
  (create with scope and optional expiry, key shown once, list with creator and last
  use, revoke, link to the API reference).
- Tests: `test_api_keys.py` covers:
  - read vs write scopes, blocked paths, wrong keys, the audit label, revocation;
  - a key following its creator (demoted → read only; removed → stops);
  - the OpenAPI security scheme and guide.
  - Plus a frontend test.
- Checks: `make check` green (SQLite 683 passed / 18 skipped; agent 30; SDKs 5 + 5;
  frontend 108). `make test-pg` 701 passed.

### 2026-11-09 — Session 9bf: 8.2 teams and verified email domains
- New models `Team`, `TeamMember` and `OrgDomain` (migration 0054, RLS).
- **Teams**:
  - A name, a **cost key** (the value of the cost "team" dimension the team owns;
    defaults to the name) and members, who must belong to the organisation.
  - API: `GET/POST/PATCH/DELETE /api/v1/org/teams` (changes need manage-members) and
    `GET /api/v1/me/teams` (used later to filter to "my team" and for scoped roles in
    8.8).
- **Verified domains**:
  - Add a domain; public mail providers (gmail.com, outlook.com …) are refused. Then
    add the TXT record `_costlens-verification.<domain>` = `costlens-verification=<token>`
    and press "Check now".
  - The lookup uses DNS-over-HTTPS (JSON API) through the app's HTTP client, so no DNS
    library is needed. New setting `COSTLENS_DNS_OVER_HTTPS_URL` (default Cloudflare's
    free resolver); point it at an internal resolver on private networks.
  - A domain can be verified by one organisation only (checked across organisations).
  - API: `/api/v1/org/domains` (+ `/{id}/verify`). This is the base for SSO
    enforcement and auto-join (8.9).
- **UI**: Settings → Members gains a "Teams" card (create with cost key, add and remove
  people) and, for admins, a "Verified domains" card (shows the exact record to
  create, check now, remove).
- Tests: `test_teams.py` covers:
  - teams: outsiders refused, duplicates refused, membership changes, "my teams",
    members cannot create teams;
  - domains: bad and public domains, normalisation, not found then verified via a fake
    DNS-over-HTTPS server, another organisation refused.
  - Plus a frontend test.
- Checks: `make check` green (SQLite 685 passed / 18 skipped; agent 30; SDKs 5 + 5;
  frontend 109). `make test-pg` 703 passed.

### 2026-11-09 — Session 9bg: 8.3 SCIM 2.0 user provisioning
- `api/scim.py` implements SCIM 2.0 (RFC 7643/7644) under `/scim/v2` for Entra ID,
  Okta, Google and others.
  - **Users**: list (paged, `eq` filters on userName/displayName/externalId), get,
    create, PUT, PATCH (active, displayName) and DELETE.
    - Creating adds the person to the organisation with the SCIM default role (viewer,
      member or admin; never owner). It creates the CostLens account if needed.
    - `active: false` or DELETE removes them from this organisation only.
  - **Groups** are CostLens teams (8.2): create, PATCH add/remove/replace members and
    rename, delete.
  - **Discovery**: ServiceProviderConfig and ResourceTypes. Errors use the SCIM error
    schema, served as `application/scim+json`.
- **Auth**: a per-organisation `scim_…` token, hashed in organisation settings and
  shown once; a new token replaces the old one. Calls are audited with actor type
  `scim`.
- **Safety**:
  - Owners cannot be removed or changed through SCIM.
  - When the organisation has verified domains (8.2), only addresses in those domains
    can be provisioned.
  - Duplicates get 409 (uniqueness).
- **Admin**: `GET/POST/DELETE /api/v1/org/scim`. UI: a "User provisioning (SCIM)" card
  under Settings → Single sign-on (base URL, default role, token shown once, new token,
  turn off).
- **Note**: token lookup scans organisations with SCIM turned on, which is fine at
  today's size. A hashed-token index can come later if needed.
- Tests: `test_scim.py` covers:
  - the user lifecycle: create, duplicate, filter, bad filter, PATCH deactivate, gone
    from the organisation, audit;
  - owners untouched and domains enforced;
  - groups ↔ teams;
  - discovery, and turning SCIM off.
  - Plus a frontend test.
- Checks: `make check` green (SQLite 688 passed / 18 skipped; agent 30; SDKs 5 + 5;
  frontend 110). `make test-pg` 706 passed.

### 2026-11-09 — Session 9bh: 8.4 temporary elevated access
- Model `ElevatedAccess` (migration 0055, RLS, audited): user, role, reason, minutes
  (15 minutes to 72 hours), status (requested / active / denied / revoked / expired),
  requester, decider, start, expiry, end.
- `accounts/elevation.py`:
  - Ask for a higher role than your own with a reason. **Someone else** approves:
    an admin, or an owner when the request is for owner.
  - Admins can also grant directly, but never to themselves.
  - Decisions use the decider's own membership role, never an elevated one.
  - While active, the effective role is the higher of membership and grant, for
    browser sessions and for API keys (still capped by the key's scope). It stops
    working exactly at `expires_at`, with no job needed.
  - Job `access.expire_elevations` (every 5 minutes) marks lapsed grants as expired
    so the audit log shows when access ended.
  - The person, or an admin, can end access early.
- New notification event `access_request` posts requests to Slack/Teams; it is listed
  under Settings → Notifications.
- API: `GET/POST /api/v1/org/elevated-access`, `/grant`, `/{id}/approve`,
  `/{id}/deny`, `/{id}/revoke`. Members see only their own requests; admins see all.
- UI: a "Temporary access" card under Settings → Members (ask with role, duration and
  reason; admins approve or deny; end early).
- Fix: the audit noun for tables ending in "ss" (`elevated_access`) was cut to
  "elevated_acces".
- Tests: `test_elevation.py` covers:
  - viewer denied, request, cannot approve without rights, approve, the elevated
    write works, double decision refused;
  - an admin cannot grant owner;
  - expiry stops access and is marked and audited;
  - an owner grants and the person ends it early;
  - no self-grant.
  - Plus a frontend test.
- Checks: `make check` green (SQLite 690 passed / 18 skipped; agent 30; SDKs 5 + 5;
  frontend 111). `make test-pg` 708 passed.

### 2026-11-09 — Session 9bi: 8.5 partner mode
- `organizations` gains `is_partner` and `partner_id` (migration 0056; `is_partner`
  defaults to false on the server for existing rows).
- **Access** (`accounts/partners.py`):
  - Admins and owners of a partner reach every client organisation, with the role
    stored on the link: **owner** for clients the partner created, **admin** for
    organisations that linked themselves.
  - Sign-in, organisation switching and `/me` (the organisation switcher) include
    these clients; the client's own memberships are unchanged.
  - Access ends as soon as the client leaves or the person stops being partner staff.
- **Partner API** (`/api/v1/partner/...`; partner admins only):
  - list clients (this month, last month, last month re-billed);
  - create a client;
  - set the markup percentage per client;
  - **re-billing** for a month: cost per service, re-billed amount per line rounded
    to the cent, totals and margin. Only the partner sees cost and margin; client
    statements as branded PDFs come in 8.10;
  - **branding** (display name, colour);
  - **link codes** (`clp_…`, hashed, valid 7 days).
- **Client side**:
  - `POST /api/v1/org/partner` (owners) gives a partner access with a code;
    `DELETE` removes it.
  - `GET /api/v1/org/branding` returns the partner's branding.
  - The app header shows the partner's name and colour instead of CostLens.
- **Becoming a partner**: `PUT /api/v1/platform/organizations/{id}/partner`, for
  platform admins (Yugiverse staff) only. Self-serve or approved sign-up comes in 8.13.
  API keys cannot call platform endpoints or change partner links.
- **UI**:
  - Settings → "Partner" tab for partner admins: clients table with markup editing,
    re-billing, "Open" and "Link an existing organisation".
  - A "Branding" form.
  - A "Partner" card under Settings → General for client owners (give or remove
    access).
- Tests: `test_partners.py` covers:
  - create a client, see it in the switcher, switch in and act as owner;
  - re-billing maths per line;
  - markup update and branding validation; the client sees the branding;
  - link code: no access before consent, a bad code, admin role after linking, a
    non-partner refused, access gone after leaving;
  - only platform staff can mark partners.
  - Plus a frontend test. The `/me` expectation in `test_demo.py` gained the two
    fields.
- Checks: `make check` green (SQLite 693 passed / 18 skipped; agent 30; SDKs 5 + 5;
  frontend 112). `make test-pg` 711 passed.

### 2026-11-09 — Session 9bj: 8.6 opt-in automatic fixes
- This is the only code that changes a customer's cloud (`clouds/aws/fixes.py`). The
  standing rule "read-only unless the customer explicitly turns on an automatic fix" is
  enforced by every guard below.
  - **A separate role**, `CostLensFixes`, with its own CloudFormation template. It
    allows only `ec2:StopInstances`, `ec2:DeleteVolume`, `ec2:ReleaseAddress` and
    `ec2:CreateSnapshot`, conditioned on
    `aws:ResourceTag/costlens-protect != "true"`, so IAM itself refuses protected
    resources. It may tag only the snapshots it creates, and can describe resources to
    re-check them. No terminate, no IAM, nothing else. The read-only role is refused
    as the fixes role.
  - **Owner opt-in**, with an explicit confirmation and an expiry (1–90 days). It has a
    daily action limit and a monthly spend limit (for purchases in 8.11; deletions
    commit nothing). The role must be assumable before saving. Any admin can turn it
    off at once; once expired, it acts as if turned off.
  - **Two people**: a member asks for a fix on a finding; a different admin approves.
    The requester can cancel; others can reject.
  - **Fixable findings**: unattached EBS volumes (snapshot, then delete), idle Elastic
    IPs (release) and idle EC2 instances (stop; only the rightsizing "idle" findings,
    never Auto Scaling group members). The fix is only offered when the finding's
    account has fixes turned on.
  - **Re-checked just before acting**: the volume must still be unattached, the address
    not associated, the instance running, and none tagged `costlens-protect=true`.
    Otherwise the fix fails with the reason and nothing changes.
  - **Snapshot before deleting**, tagged `created-by=costlens` and with the fix ID.
  - **Audited** (`FixAction` model, migration 0057, RLS). On success the finding moves
    to "applied", with a history note and the snapshot ID.
- **Runs** from job `fixes.run_approved` every minute. Approved fixes are read from
  the database rather than enqueued by the approving request, so a fix can never run
  before its approval is committed (the in-process runner would otherwise race it).
- API:
  - `GET/POST/DELETE /api/v1/connections/{id}/fixes` (POST: owners only, with
    `confirm: true`);
  - `POST /api/v1/findings/{id}/fix`;
  - `GET /api/v1/fixes`, `POST /api/v1/fixes/{id}/approve` (admins) and `/reject`.
- UI:
  - An "Automatic fixes" dialog on active AWS connections (template, role, days, daily
    limit, confirmation checkbox; turn off now).
  - "Ask CostLens to fix this" on fixable savings.
  - An "Automatic fixes" list on the Savings page with approve, reject or cancel, the
    error or snapshot, and who asked and approved.
  - The always-visible banner while fixes are on is 8.12.
- **Limitation (v1)**: the fixes role lives in the connection's own account. Findings
  in other linked accounts need a connection for that account.
- Tests: `test_fixes.py`, with a fake EC2 recording every change, covers:
  - opt-in: confirmation required, separate role required, role check, owners only,
    policy conditions, no terminate;
  - two people, the re-check, snapshot before delete, finding marked applied;
  - guards: in use again, protect tag, turned off after approval (no changes);
  - the daily limit, and expiry.
  - Plus a frontend test.
- Checks: `make check` green (SQLite 697 passed / 18 skipped; agent 30; SDKs 5 + 5;
  frontend 113). `make test-pg` 715 passed.

### 2026-11-09 — Session 9bk: 8.7 read-only MCP server
- `api/mcp.py`: Model Context Protocol over Streamable HTTP at `POST /mcp` (JSON-RPC
  2.0, single JSON responses), written without a new dependency.
  - Methods: `initialize` (protocol 2025-06-18, falling back to 2025-03-26 /
    2024-11-05; tools capability; instructions), `notifications/initialized` (202),
    `ping`, `tools/list`, `tools/call`. Unknown methods get -32601; bad JSON -32700;
    batches are refused.
  - **Auth**: an organisation API key (8.1; a Read key is enough), with the same
    resolution, role, rate limits and audit as the REST API. No cookie, no CSRF.
- **Tools (all read-only)**:
  - `cost_summary`, `explore_costs` (group, filter and days, validated by the
    report-builder schema, so no SQL), `top_cost_changes`;
  - `list_savings` (with steps), `list_anomalies`, `budgets`.
  - Results come as JSON text plus `structuredContent`. Bad arguments or an unknown
    tool return `isError: true` instead of failing.
- **UI**: Settings → API keys gains "Connect an AI assistant (MCP)" with the server URL
  and a ready-to-copy configuration. The one write tool, creating an investigation,
  comes in 8.14.
- Tests: `test_mcp.py` covers the handshake, notifications, ping, the tool list, real
  calls on seeded costs, invalid queries and unknown tools as tool errors, unknown
  methods, wrong keys (401) and ISO dates. Plus the frontend snippet.
- Checks: `make check` green (SQLite 700 passed / 18 skipped; agent 30; SDKs 5 + 5;
  frontend 113). `make test-pg` 718 passed.

### 2026-11-09 — Session 9bl: 8.8 custom roles from permission sets, scoped to accounts/teams
- Model `CustomRole` (migration 0058, RLS): name, description, permissions, and an
  optional list of accounts and/or teams. A membership may point to one
  (`custom_role_id`).
- **Permissions**: when a custom role is set, its permission set replaces the built-in
  role's in every `require()` check. Reading is always included.
  - Grantable: read, write, manage members, manage connections, manage settings.
    Owner powers (billing, owners, deleting the organisation) are never grantable.
  - Owners cannot be given a custom role, and admins cannot change their own.
  - Active temporary access (8.4) replaces the custom role while it lasts.
  - API keys made by someone with a custom role are limited by it as well (still
    within the key's own scope).
- **Scope (accounts/teams)**, safe by default:
  - A scoped role can open only the views that apply the scope: its session and `/me`,
    the cost explorer (filters narrowed to the allowed accounts/teams; asking for
    others returns nothing), filter values (accounts/teams narrowed; service, region,
    charge and pricing allowed; resources and tags refused), saved views, and savings
    (list and details narrowed; others are "not found").
  - **Every other data view returns 403** for a scoped role, so nothing leaks through
    views that do not know about scopes. More views can be opened to scoped roles one
    at a time as they learn the scope.
- `/api/v1/auth/me` returns `permissions` and `scope` when a custom role applies.
- API:
  - `GET/POST /api/v1/org/custom-roles`, `PUT/DELETE /{id}`; deleting a role puts
    members back on their built-in role;
  - `PUT /api/v1/org/members/{user_id}/custom-role`.
- UI: a "Custom roles" card under Settings → Members (permission checkboxes in plain
  words, optional accounts and teams, assign per member).
- Fixes along the way:
  - PostgreSQL refuses NUL in text, so the "match nothing" filter value is now
    `~costlens-no-access~`.
  - A flaky partner settings test waited for the table rather than its rows.
- Tests: `test_custom_roles.py` covers:
  - grantable permissions only; owners refused;
  - a scoped role: no settings, explorer narrowed (including a sneaky filter), filter
    values narrowed and resource values refused, savings narrowed, others 404,
    unscoped views 403;
  - removing the role restores access;
  - an unscoped custom role (manage settings without write);
  - deleting a role.
  - Plus a frontend test.
- Checks: `make check` green (SQLite 702 passed / 18 skipped; agent 30; SDKs 5 + 5;
  frontend 114). `make test-pg` 720 passed.

### 2026-11-09 — Session 9bm: 8.9 enforced SSO and auto-join for verified domains
- `accounts/domain_policy.py`, stored in organisation settings (`domain_policy`):
  - **Enforce SSO**:
    - People whose email is in one of the organisation's verified domains (8.2) must
      sign in through the organisation's own enabled provider(s) (`org:<id>`).
    - The shared Google/Microsoft sign-in and local login are refused with
      `sso_required` (a clear message on the sign-in page).
    - **Owners are exempt** as a break-glass in case the identity provider is down.
    - It can only be turned on with a verified domain and at least one enabled
      organisation provider, so nobody gets locked out by mistake.
  - **Auto-join**: people from a verified domain who sign in by any allowed method
    become members with the chosen default role (viewer, member or admin; never
    owner). People already in the organisation are unchanged.
  - Only one organisation can verify a domain, so each person maps to at most one
    policy.
- API: `GET/PUT /api/v1/org/domain-policy` (manage-members permission).
- UI: the "Verified domains" card gains "For people from verified domains" switches
  (require own sign-in; auto-join as a role), shown once a domain is verified.
- Tests: `test_domain_policy.py` covers:
  - preconditions (verified domain, an organisation provider, owner role refused);
  - through the real Google sign-in flow with the fake identity provider: a member
    and a newcomer from the domain are refused, the owner gets in (break-glass), other
    domains are unaffected;
  - auto-join with the default role, and nothing for other domains.
  - Plus a frontend check.
- Checks: `make check` green (SQLite 705 passed / 18 skipped; agent 30; SDKs 5 + 5;
  frontend 114). `make test-pg` 723 passed.

### 2026-11-09 — Session 9bn: 8.10 partner portfolio and branded client statements
- `GET /api/v1/partner/portfolio` lists clients sorted by an attention score, with the
  reasons spelled out:
  - failing cloud connections (+50 each);
  - budgets forecast over 100% (+30 each);
  - open unusual cost increases this month (+10 each);
  - spend pace at least 20% above last month (+growth/4, capped);
  - open savings (+1 per 100 a month, capped at 20).
- `GET /api/v1/partner/clients/{id}/statement.pdf?month=` (`reports/rebill.py`): the
  client's statement in the **partner's branding** (display name, colour on title and
  table header).
  - It lists **only re-billed amounts** per service and the total. The partner's cost,
    markup and margin are never passed to the PDF builder, and a test checks both its
    arguments and the PDF text.
- UI: the Partner tab gains "Needs attention" (ordered clients with reasons and
  "Open"), and the re-billing panel links to the client's PDF statement.
- Tests: `test_partners.py` gains portfolio ordering (a client with a failing
  connection first, with its reason) and the statement (branding, client, month,
  re-billed lines only, no cost or margin anywhere in the text). The frontend partner
  test was extended, and a duplicate-text query fixed.
- Checks: `make check` green (SQLite 706 passed / 18 skipped; agent 30; SDKs 5 + 5;
  frontend 114). `make test-pg` 724 passed.

### 2026-11-09 — Session 9bo: 8.11 auto clean-up and approved purchases through the fixes role
- **Auto clean-up**: a new opt-in (`auto_request`) on automatic fixes (8.6).
  - Job `fixes.file_requests` (daily 08:30) files a fix request for every fixable
    finding in the account that has stayed open for at least **7 days** and has no
    request already. These requests have no human requester and are shown as
    "CostLens (auto clean-up)".
  - **Nothing runs until a person approves**, and every 8.6 guard still applies at run
    time (re-check, protect tag, daily limit, expiry).
  - A new `fix_request` notification event tells Slack/Teams how many clean-ups are
    waiting.
  - `fix_actions.requested_by_id` is now nullable (amended in 0057, still uncommitted;
    the PostgreSQL test database was reset).
- **Approved Savings Plan purchases**:
  - `POST /api/v1/purchases/{id}/buy` turns an **approved** Savings Plan in the
    purchase plan (4.20) into a fix action (`purchase_savings_plan`). It still needs a
    second person's approval.
  - The commitment it makes (hourly × hours in the term) counts against the **monthly
    spend limit**.
  - At run time it checks the plan is still approved, finds the matching Compute
    Savings Plan offering (term, payment option, USD), and buys it with
    `clientToken = fix id`, so a retry can never buy twice. It tags the plan
    `created-by=costlens` and marks the planned purchase "purchased".
  - The fixes role policy gains `savingsplans:DescribeSavingsPlansOfferings` and
    `savingsplans:CreateSavingsPlan`. EC2 reserved-instance purchases are not
    automated (Savings Plans only).
- **UI**:
  - The opt-in dialog gains "Purchases a month at most" and the auto clean-up
    checkbox.
  - Approved Savings Plans get "Buy automatically".
  - The purchase-plan text explains both ways to buy.
- Tests (`test_fixes.py`, +2):
  - auto clean-up files only week-old findings, never twice, does nothing until
    approved, then runs;
  - purchase: not buyable until approved; the spend limit blocks it (no call to AWS);
    within the limit it buys once with the right offering, commitment and idempotency
    token, and marks the plan purchased.
  - Plus a frontend check.
- Checks: `make check` green (SQLite 708 passed / 18 skipped; agent 30; SDKs 5 + 5;
  frontend 114). `make test-pg` 726 passed on a fresh database.

### 2026-11-09 — Session 9bp: 8.12 write-access banner
- `GET /api/v1/org/write-access` (any member) lists the AWS connections where automatic
  fixes are currently on (not expired): name, account, who turned it on, when, until
  when, and whether auto clean-up is on.
- `WriteAccessBanner` sits in the app shell, under the demo banner, on every page while
  any account has write access:
  - "CostLens can change resources in <account>", who turned it on and when, the
    expiry, and that each change needs a second person;
  - a one-click **Turn off** for admins (the existing
    `DELETE /api/v1/connections/{id}/fixes`). The change is in the audit log as an
    update to the connection by that admin.
- Tests:
  - backend: an empty list before; after turning on, any member sees the owner's
    name; members cannot turn it off; an admin can; the list empties; the audit
    records the admin;
  - frontend: the banner text on the overview, and Turn off calls the API.
- Checks: `make check` green (SQLite 709 passed / 18 skipped; agent 30; SDKs 5 + 5;
  frontend 115). `make test-pg` 727 passed.

### 2026-11-09 — Session 9bq: 8.13 partner sign-up, invitation links, logo upload
- **Partner sign-up**: new instance setting `COSTLENS_PARTNER_SIGNUP` = `approval`
  (default) | `self_serve` | `off`.
  - Organisation owners apply (`POST /api/v1/org/partner-application`: company, website,
    message). With approval the application waits for Yugiverse; self-serve makes the
    organisation a partner at once; off refuses. A partner's client cannot apply.
  - Platform admins list pending applications
    (`GET /api/v1/platform/partner-applications`) and approve or reject
    (`POST .../{org_id}`); the decision records who made it.
- **Client invitation links**: partner link codes (8.5) now **work once** (spent on
  use) and still **expire after 7 days**.
  - `/partner-invite/<code>` shows who is inviting (with their logo). The person picks
    an organisation they own, or creates a new one, which is then put under the
    partner with admin access and opened.
  - API: `GET /api/v1/partner-invites/{code}` (does not spend the code) and
    `POST .../accept`. API keys cannot use invitations or applications.
- **Logo upload** (`core/images.py`): `PUT /api/v1/partner/logo` (raw body, 200 KB at
  most); `DELETE` removes it.
  - The format is recognised from the **bytes** (PNG, WebP, SVG), never from the name
    or content type.
  - **SVGs are rebuilt from an allow-list** of drawing elements and attributes. Scripts,
    `on*` handlers, external `url(...)` and `href`, `<image>`, `<a>` and
    `foreignObject` are dropped, and DOCTYPE/ENTITY declarations are refused before
    parsing.
  - The logo is stored as a data URL in the partner's branding and shown with `<img>`
    (where SVG scripts never run anyway). Saving the name and colour now keeps the
    logo.
- **UI**:
  - logo upload under Partner → Branding; the logo in the header for clients;
  - the invitation link (instead of a bare code) and the invitation page;
  - a "Become a partner" card for owners under Settings → General.
- Tests: `test_partner_signup.py` covers:
  - SVG sanitising against scripts, handlers, external references, `foreignObject`,
    `javascript:` and entities;
  - formats by bytes, and the size limit;
  - upload, then branding with a clean logo;
  - invitation accept creating a linked organisation, single use, and expiry;
  - sign-up modes approval (with platform review), self-serve and off.
  - Plus frontend tests (invitation page, expired link, application).
- Checks: `make check` green (SQLite 714 passed / 18 skipped; agent 30; SDKs 5 + 5;
  frontend 118). `make test-pg` 732 passed.

### 2026-11-09 — Session 9br: 8.14 MCP may start a Kubernetes investigation
- API keys gain a separate permission, **`can_investigate`** (off by default; a column
  added to migration 0053, which is still uncommitted; the PostgreSQL test database was
  reset). It is set when the key is created ("AI assistants may start Kubernetes
  investigations").
- MCP `tools/list` offers **`create_investigation`** only to keys with that
  permission. It takes cluster name or ID, namespace, kind, name and hours (≤ 72).
  - It runs the normal investigation (events, restarts, metrics, changes, ranked
    causes), saves it with `trigger = "mcp"` and the key's creator, and returns the
    title, summary, top causes and a link.
  - It is **audited as the key** ("API key <name> … for <creator>").
  - It is the **only write** MCP allows. A read key with the permission still cannot
    use the REST `POST /k8s/investigations` (needs write), and without the permission
    the tool is neither listed nor callable.
- Section 8 is complete.
- Tests: `test_mcp.py` covers hidden without the permission, listed and working with
  it, an unknown cluster as a tool error, the saved investigation, the audit label, and
  REST still refused.
- Checks: `make check` green (SQLite 715 passed / 18 skipped; agent 30; SDKs 5 + 5;
  frontend 118). `make test-pg` 733 passed on a fresh database.

### 2026-11-09 — Session 9bs: 9.1 guided setup and next-steps checklist
- `GET /api/v1/org/setup` works out each step from what the organisation has actually
  done, with nothing to tick by hand:
  - connect a cloud account (an active connection);
  - see the first costs (any cost rows);
  - act on a saving (any finding not open);
  - set a budget;
  - send alerts to Slack or Teams (an enabled channel);
  - invite the team (more than one member);
  - connect a Kubernetes cluster (optional).
  - It returns done/total for the required steps and the **next** step.
  - `POST /api/v1/org/setup/hide` (admins) hides the checklist.
- UI:
  - A "Getting started: N of M done" card on the Overview: progress meter, compact
    steps, "Do this next" on the next step, "Hide this list" for admins. It
    disappears when everything is done, and never shows in the demo.
  - A guided `/setup` page: each step with why it matters, a link to where it is done,
    and "Check again".
- Tests: `test_setup.py` (the checklist following real actions; hide) and a frontend
  test (next step highlighted and linked; progress).
- Checks: `make check` green (SQLite 716 passed / 18 skipped; agent 30; SDKs 5 + 5;
  frontend 119). `make test-pg` 734 passed.

### 2026-11-09 — Session 9bt: 9.2 plain-English explanations and glossary
- `lib/glossary.ts`: 25 cloud and FinOps words written for finance readers (amortised,
  billed and effective cost, on-demand, Savings Plans, reservations, coverage,
  utilisation, credits, forecast, anomaly, showback, chargeback, shared costs, unit
  cost, tags, virtual tags, right-sizing, idle, spot, Graviton, egress, FOCUS, list
  price, partner margin). Each has a one-line meaning and, where useful, a longer
  explanation.
- `/glossary` page: searchable, alphabetical, and each word has an anchor
  (`/glossary#amortised`).
- `<Term id>` component: a dotted-underlined word whose meaning shows on hover and is
  read by screen readers (`aria-description`); it links to the glossary entry.
  - Used on Costs ("What is amortised or billed?") and Executive ("Words on this
    page").
- The user menu gains "Guided setup" and "Glossary".
- Tests: a frontend glossary test (meanings shown, search filters). Frontend only, so
  `make test-pg` was not re-run (734 at 9.1).
- Checks: `make check` green (SQLite 716 passed / 18 skipped; agent 30; SDKs 5 + 5;
  frontend 120).

### 2026-11-09 — Session 9bu: 9.3 responsive and accessibility audit
- Contrast: `src/test/contrast.test.ts` reads the theme tokens from `index.css`,
  converts oklch to luminance and checks 14 text/background pairs in light and dark
  against WCAG AA (4.5:1). All pass.
- Screen-reader names: `src/test/accessible-names.test.tsx` renders 14 main routes and
  checks that every reachable button, link, input, select and tab has an accessible
  name (`dom-accessibility-api` 0.5.16, MIT, already in the tree through Testing
  Library, now declared as a dev dependency with a small type declaration).
- Browser audit (Vite + API, demo organisation):
  - no horizontal scrolling on 15 routes at 375 px;
  - a "Skip to content" link is the first focusable element; one `main` landmark;
    `lang="en"`; visible focus ring; no positive `tabindex`;
  - dark mode checked on Savings, Costs and Overview at phone width.
- Fixes found by the audit:
  - the local demo could be left without cost rows; `ensure_demo` now re-seeds an
    empty demo (`test_an_empty_demo_is_seeded_again`);
  - Costs at phone width: the helper text under "Show" pushed the selects out of
    line; the glossary links moved into the description line;
  - audit log "Details" (noted at 1.13): bookkeeping fields (`id`, `org_id`,
    timestamps) are hidden, ids are shortened, field names read as words and created
    rows show only the new value.
- Checks: `make check` green (SQLite 717 passed / 18 skipped; agent 30; SDKs 5 + 5;
  frontend 148). `make test-pg` 735 passed.

### 2026-11-09 — Session 9bv: 9.4 in-app notification centre
- One notification service: `notify()` now puts every event in the inbox of the
  people it concerns (`core/inbox.py`), emails those who asked for it, then sends to
  Slack/Teams channels and outgoing webhooks as before.
  - Who receives what: alerts, digests, incidents and finished imports go to every
    member; approvals waiting (purchases, fixes, access requests) and failed imports
    go to admins and owners; invitations go only to the invited person.
  - A burst becomes at most 10 inbox rows per event. Notifications are a retention
    kind (`notifications`: 90 days by default, 7–365) purged by the nightly retention
    job, as the design notes require for every time-series table.
- Model `InboxItem` (migration 0059, RLS with the user column, so a person reads
  their own notifications from every organisation). `Membership.notification_prefs`
  holds personal choices per organisation (`inbox_off`, `email`); it is left out of
  the audit log.
- New events:
  - `import_finished` / `import_failed`: file uploads always; cloud connections only
    on a change (first costs in, starts failing, working again), so routine imports
    every 6 hours stay quiet.
  - `invitation`: someone who already has an account also sees the invitation in
    their inbox. The secret link stays only in the email.
- API (`/api/v1/me`): `GET notifications` (newest first, `unread_only`, `before`
  cursor), `GET notifications/unread`, `POST notifications/{id}/read`,
  `POST notifications/read-all`, `GET/PUT notification-preferences` (admin-only
  kinds are hidden from others; unknown kinds are refused).
- UI: a bell in the header with the unread count (polled every minute) and the latest
  8 notifications, plus "Mark all read"; a `/notifications` page with the inbox
  (unread filter, open, mark read, older pages) and a table of personal choices
  (inbox and email per kind).
- Tests: `test_inbox.py` (who receives what, the API and badge, preferences and email,
  invitations across organisations, import notices, retention) and a frontend test (bell,
  page, choices). `/notifications` added to the accessible-names test.
- Checks: `make check` green (SQLite 723 passed / 18 skipped; agent 30; SDKs 5 + 5;
  frontend 152). `make test-pg` 741 passed.

### 2026-11-09 — Session 9bw: 9.5 first savings fast, trial countdown, export check
- First savings fast:
  - AWS connections used to wait up to 6 hours for their first import. Now the
    organisation's first connection (AWS verify, Azure, Google Cloud, SaaS) queues
    `onboarding.first_run`: import, the cloud's own recommendations, every detector,
    then an inbox/Slack/Teams notification "First savings ready: about X a month"
    (new event `first_savings`). A failing step is logged and the run still finishes.
  - Later AWS connections import right after verifying (before: only on schedule).
  - `GET /api/v1/org/setup/first-savings`: stage (connect → importing → analysing →
    ready / none_found), total (overlapping savings counted once), top 3; scoped
    custom roles see only their findings.
  - UI: a "First savings" panel in the Getting-started card and on `/setup`; it
    refreshes every 10 seconds while the first run is working.
- Trial countdown:
  - `COSTLENS_TRIAL_DAYS` (default 14, a placeholder until Q2), SaaS mode only; demo
    organisations and partner clients have none. Set when an organisation is created
    through the API.
  - `GET /api/v1/org/trial` (none / active / ending in the last 3 days / ended) and a
    banner under the header. What happens when a trial ends comes with B.2.
- Live billing-export check:
  - `POST /api/v1/connections/{id}/export-check` lists the AWS S3 export or the Azure
    storage export with the connection's read-only access (downloads nothing). It
    reports in plain words: readable with N files and the months covered, readable
    but empty (first delivery takes up to 24 hours), bucket missing, or access denied
    with the permission to add. The result is kept and shown on the connection.
  - UI: "Check export" on connections with an export.
- Tests: `test_onboarding.py` (first run and notification, a second connection, stages,
  AWS and Azure export checks, trial countdown and API) and frontend tests (first
  savings panel, importing state, trial banner, export check).
- Checks: `make check` green (SQLite 729 passed / 18 skipped; agent 30; SDKs 5 + 5;
  frontend 156). `make test-pg` 747 passed.

### 2026-11-09 — Session 9bx: B.1 plans, entitlements and limits
- `billing/plans.json`: the plan catalogue as data, clearly marked **placeholder**
  (Free, Team, Business, Enterprise; features and limits only, no prices) until Q2.
  `COSTLENS_PLANS_FILE` points at another file; the catalogue is validated (unknown
  features or limits, or default/trial plans that do not exist, are refused).
- `billing/plans.py`: which plan applies — local mode and demo organisations get
  everything; SaaS uses the organisation's plan, else the trial plan while a trial
  runs (9.5), else the default; self-hosted uses the free tier until licences (B.6).
- Features gated on the routes that switch them on or add to them (402
  `plan_feature`): SSO, SCIM token, custom roles, API keys, MCP, automatic fixes (enable,
  request, buy, approve), outgoing webhooks, ticket/PR integrations, partner clients
  and link codes, "ask in plain words", adding Kubernetes clusters.
- Limits checked before adding (402 `plan_limit`; `billing/usage.py`): people (members
  plus open invitations; also SCIM and domain auto-join, which skips quietly when full),
  connections, clusters, longest retention. Spend under management (last 30 days in
  USD) is measured and shown; API calls and AI summaries are measured with B.12.
  Over a limit nothing is deleted or hidden, so a downgrade loses no data.
- API: `GET /api/v1/plans` (catalogue), `GET /api/v1/org/plan` (plan, where it comes
  from, features, usage per limit), `PUT /api/v1/platform/organizations/{id}/plan`
  (staff only, until subscriptions set plans in B.2).
- UI: Settings → Plan: usage against each limit with meters, included features, and a
  note that the plans are provisional.
- Tests: `test_plans.py` (catalogue validation, which plan applies, own catalogue file,
  limits, feature gates, staff switch, local mode) and a frontend test.
- Checks: `make check` green (SQLite 736 passed / 18 skipped; agent 30; SDKs 5 + 5;
  frontend 157). `make test-pg` 754 passed.

### 2026-11-09 — Session 9by: B.2 subscriptions (provider-agnostic core)
- Model `Subscription` (migration 0060, one per organisation, RLS, audited): plan,
  status (`trialing`, `active`, `past_due`, `ended`), monthly/annual, provider
  (`manual` now; Razorpay/Stripe with B.3/B.4), period, trial end, cancel at period
  end, scheduled downgrade.
- `billing/subscriptions.py`:
  - The 9.5 trial is now a `trialing` subscription of the catalogue's trial plan
    (the countdown reads it).
  - Upgrades (a bigger plan, or paying after a trial) need a payment. Until the
    payment providers exist, customers get "contact us" (409 `payment_unavailable`)
    and staff record an invoiced `manual` subscription.
  - Downgrades need no payment and are applied at the end of the paid period;
    choosing the current plan again drops a scheduled downgrade. Moving to the free
    plan during a trial ends the trial.
  - Cancel keeps the plan until the period ends (a trial ends at once); resume undoes
    it. Ended subscriptions fall back to the free plan; no data is deleted (B.1 only
    refuses to add more).
  - Hourly job `billing.roll_periods`: ends trials and cancelled subscriptions, applies
    scheduled downgrades, renews invoiced subscriptions; provider subscriptions renew
    through their webhooks (B.5). Admins get a `billing` notification for each change.
- `plan_for` reads the subscription; the B.1 staff switch now records a manual
  subscription (or ends it).
- API: `GET /api/v1/org/subscription`, `POST .../change`, `POST .../cancel`,
  `POST .../resume` (owners: `manage_billing`).
- UI: a Subscription card on Settings → Plan: what applies and until when, choose a
  plan and monthly/yearly, cancel / keep, "End the trial now", and a note that
  upgrades go through us until online payment is available.
- Tests: `test_subscriptions.py` (month arithmetic, trial ending, free plan during a
  trial, paid plan with downgrade, renew, cancel and resume, owners only, no
  subscription in local mode); trial tests updated; frontend tests for the card.
- Checks: `make check` green (SQLite 742 passed / 18 skipped; agent 30; SDKs 5 + 5;
  frontend 159). `make test-pg` 760 passed.

### 2026-11-09 — Session 9bz: B.5 signed, de-duplicated payment webhooks
- `POST /api/v1/billing/webhooks/{razorpay|stripe}` (404 unless that provider's secret
  is set: `COSTLENS_RAZORPAY_WEBHOOK_SECRET`, `COSTLENS_STRIPE_WEBHOOK_SECRET`).
- Signatures checked with the standard library against each provider's published
  scheme (no SDK): Razorpay HMAC-SHA256 of the raw body; Stripe `t=…,v1=…` over
  `"<t>.<body>"`, with deliveries older than 5 minutes refused as replays.
- Model `PaymentEvent` (migration 0061; platform-level, holds only ids, type and
  outcome, never card or bank details): unique per (provider, event id), so a repeated
  delivery answers `duplicate` and changes nothing (simultaneous repeats too).
- Events mapped to the B.2 subscription: activated (organisation, plan and interval
  from the checkout's notes/metadata, which B.3/B.4 will set), renewed (period moved
  on), payment failed (`past_due` + a `billing` notification; retries and grace come
  with B.16), cancelled (ended). Unknown subscriptions and other event types are
  recorded and answered 200 so providers stop retrying.
- Tests: `test_payment_webhooks.py` (Razorpay and Stripe lifecycles, duplicates, bad and
  old signatures, missing checkout details, unconfigured providers).
- Checks: `make check` green (SQLite 748 passed / 18 skipped; agent 30; SDKs 5 + 5;
  frontend 159). `make test-pg` 766 passed.

### 2026-11-09 — Session 9ca: B.6 self-hosted licence
- `billing/licence.py`: keys are `CLv1.<payload>.<signature>` (base64url). The payload
  is JSON (id, customer, plan, own limits and extra features, issued, expires, grace
  days); the signature is Ed25519 (the `cryptography` package already in use).
- Only Yugiverse's **public** keys are built in (`PUBLIC_KEYS`, newest first so keys can
  be rotated). They are deliberately not a setting: otherwise anyone could trust their
  own key and sign licences. The list is empty until the production key pair is made
  (new open question **Q14**), so for now every self-hosted install runs the free tier.
- States: valid → grace (expired but working for `grace_days`) → expired (free tier;
  nothing deleted). Forged, tampered, damaged or newer-format keys are refused.
- Where the key comes from: `COSTLENS_LICENCE_KEY`, else `COSTLENS_LICENCE_FILE`, else
  one installed with `costlens licence install FILE` or `PUT /api/v1/system/licence`
  (platform admins; checked before saving). `GET /api/v1/system/licence` shows the
  state, days left and grace (the B.14 page will build on it).
- In self-hosted mode the plan is the licence's plan with its own limits and extra
  (known) features, else the free tier.
- New CLI `costlens platform-admin add|remove|list EMAIL`: until now nothing could make
  someone a platform admin, which self-hosted installs need for the licence.
- Tests: `test_licence.py` (signature, forgery, tampering, format, rotation, no built-in
  key, states, plan from key/file/grace/expiry, API install, CLI).
- Checks: `make check` green (SQLite 755 passed / 18 skipped; agent 30; SDKs 5 + 5;
  frontend 159). `make test-pg` 773 passed.

### 2026-11-09 — Session 9cb: B.7 licence issuing tool
- `tools/licence-issuer/issue.py`, a separate script (only needs `cryptography`):
  - `keygen --out FILE [--passphrase-env VAR]`: makes the Ed25519 key pair; the private
    key file is created readable by its owner only and never overwritten; prints the
    public key to build into the product (Q14).
  - `issue --key-file FILE | env COSTLENS_LICENCE_PRIVATE_KEY --customer --plan --days
    [--grace-days] [--limit name=N|unlimited] [--feature x] [--out FILE]`.
  - `verify --public-key=KEY FILE` (with "=": keys may start with "-").
  - KMS signing is not built: no cloud KMS is set up for Yugiverse yet (Q7).
- Never in the image or the repository: `tools/licence-issuer`, `*.key` and `*.licence`
  added to `.dockerignore`; `*.key` and `*.licence` added to `.gitignore`.
- Tests: `test_licence_issuer.py` (key file permissions and no overwrite; a licence
  issued by the tool verifies in the product with limits and features; key from the
  environment with a passphrase; the tool is excluded from the image).
- Checks: `make check` green (SQLite 758 passed / 18 skipped; agent 30; SDKs 5 + 5;
  frontend 159). `make test-pg` 776 passed.

### 2026-11-09 — Session 9cc: B.9 public website
- `website/`: a static site with no framework, no tracking and no third-party requests
  (one stylesheet; light and dark; phone width checked in the browser): Product,
  Pricing, Getting started, Security, Support.
  - Content describes only what is built (connections, savings, budgets, Kubernetes,
    finance views, read-only access, RLS, encryption at rest, SSO/SCIM, audit log,
    signed images).
  - Pricing: the plans table is generated from `billing/plans.json` by
    `tools/website_plans.py` (`--check` in `make check` and CI), with a notice that
    names, limits and prices are not final. No prices are shown (a test enforces it
    until Q2).
  - Support and security contacts say "being set up" until Q6.
- `.claude/launch.json` gains a `website` preview (python http.server on port 4180).
- Tests: `test_website.py` (pages exist; one h1, `lang`, viewport, skip link first;
  every internal link resolves; nothing external; no prices).
- Checks: `make check` green (SQLite 765 passed / 18 skipped; agent 30; SDKs 5 + 5;
  frontend 159; website plans up to date). No backend code changed, so `make test-pg`
  was not re-run (776 at B.7).

### 2026-11-09 — Session 9cd: B.10 legal drafts
- `legal/drafts/`: Terms of Service (SaaS), EULA (self-hosted), Privacy Notice, Data
  Processing Agreement (with a security-measures annex describing what is actually
  built), SLA, Refund and Cancellation Policy, and a README listing the placeholders
  and the points for the lawyer (DPDP Act 2023 and GDPR roles, transfers, liability caps,
  RBI e-mandates, GST, export controls, consistency with LICENSE).
- Every draft starts with "DRAFT — NOT REVIEWED BY A LAWYER. NOT FOR PUBLICATION.";
  owner decisions are [BRACKETED] placeholders (entity, address, emails, governing law,
  prices, trial, hosting and email subprocessors, SLA targets).
- Not published: the website does not link them and `legal` is in `.dockerignore`
  (`test_legal_drafts.py` checks both and the banners).
- Checks: `make check` green.

### 2026-11-09 — Session 9ce: B.11 documentation
- `docs/`: README (index), architecture (modes, processes, code layout, tenancy, data
  flow, jobs), installation (self-hosted Compose step by step, updating, Kubernetes
  agent, AWS principal; SaaS checklist pending Q7), security for operators (keys and
  what to do if lost or leaked, database roles, cloud access, network, sign-in, rate
  limits, logs, licences), public API (API keys and scopes, problem+json codes, rate-limit
  headers, useful endpoints, outgoing webhook signatures, MCP tools), development and
  releasing (make targets, generated files, migration recipe, CI, tagged releases).
- `docs/configuration.md` is generated from `config.py` by `tools/config_reference.py`
  (`--check` in `make check` and CI). Settings that had no comment got one, so every
  variable is described.
- Tests: `test_docs.py` (guides exist, relative links resolve, every setting documented
  with a description).
- Checks: `make check` green (SQLite 770 passed / 18 skipped; agent 30; SDKs 5 + 5;
  frontend 159). Only comments changed in backend code, so `make test-pg` was not re-run.

### 2026-11-09 — Session 9cf: B.12 usage metering
- Models `UsageCounter` (per organisation, day and metric; upsert that works on SQLite
  and PostgreSQL) and `UsageSnapshot` (one per organisation and day), migration 0062
  with RLS; both are retention kinds (`usage`, `usage_counters`: 400 days by default).
- Counted as they happen (`billing/metering.py`):
  - `api_calls`: every API-key request. Over the plan's daily limit → 429 `plan_limit`
    until midnight UTC (the refused request is not counted).
  - `ai_summaries`: each AI answer or explanation ("ask in plain words", anomaly
    explanations, Kubernetes investigation wording). Over the monthly allowance, wording
    falls back to the plain template; a direct question answers 402 `plan_limit`.
- Nightly job `usage.snapshot` (00:30 UTC): yesterday's people, connections, clusters,
  spend under management (30 days, USD), API calls and AI summaries for every
  organisation except demos; re-running rewrites the day.
- B.1 limits now show real "used" values for API calls and AI summaries.
- API: `GET /api/v1/org/usage?days=30` (now + daily history). UI: a "Usage history"
  card on Settings → Plan.
- Tests: `test_metering.py` (upsert counters per organisation, API calls counted and
  limited through the real API, AI allowance, snapshots and history, retention kinds,
  demos skipped) and a frontend assertion.
- Checks: `make check` green (SQLite 776 passed / 18 skipped; agent 30; SDKs 5 + 5;
  frontend 159). `make test-pg` 794 passed.

### 2026-11-09 — Session 9cg: B.13 changelog (status page with D.9)
- `release-notes/<version>.md`: customer-facing notes, first line
  `# <version> — <date | unreleased>`; `0.1.0.md` written (unreleased).
- `tools/changelog.py` builds `website/changelog.html` ("What's new", newest first by
  semantic version; unreleased notes hidden; a small, escaped Markdown subset with only
  https or same-site links). `--check` in `make check` and CI; the release guide says
  when to run it. Every website footer links to it.
- Status page: as the design notes say, Uptime Kuma's built-in status page, which comes
  with the uptime checks in D.9; the Support page keeps "status page is coming" until then.
- Tests: `test_changelog.py` (rendering and escaping, version order, unreleased hidden,
  file/version mismatch refused, page up to date).
- Checks: `make check` green. No backend code changed.

### 2026-11-10 — Session 9ch: B.14 licence status in the UI
- `licence.status()` adds a plain-English `warning`: 30 days before the end ("ends in
  N days"), during the grace period ("keeps working until <date>, then the free tier;
  nothing is deleted"), after it, and for a key that cannot be used.
- Settings → Plan (admins, self-hosted only): a Licence card with the state, who it is
  licensed to, end date and days left, grace end, licence id, and — for platform admins —
  a field to install a new key. Plan, limits and usage were already on that tab (B.1,
  B.12) and follow the licence.
- A banner under the header tells admins of a self-hosted installation about the
  warning, with a link to the details. `/me` now says whether the person is a platform
  admin.
- Fixed a test that mixed the local date with the UTC date
  (`test_schedules_send_and_download` failed for 5½ hours after midnight IST).
- Tests: licence warnings (before expiry, grace, expired, invalid) and `/me`; frontend
  tests for the card, the banner and installing a key, and nothing outside self-hosted.
- Checks: `make check` green (SQLite 781 passed / 18 skipped; agent 30; SDKs 5 + 5;
  frontend 161). `make test-pg` 799 passed.

### 2026-11-10 — Session 9ci: B.16 failed payments
- Retrying the charge is the payment provider's job (Razorpay and Stripe retry on their
  own schedules, set in their dashboards). CostLens follows up:
  - `Subscription.past_due_since` and `reminders_sent` (migration 0063). A failed
    payment webhook starts a past-due spell once (repeat failures do not restart it or
    re-notify); a successful payment clears it.
  - The hourly `billing.roll_periods` job now also runs `dunning()`: emails the
    organisation's **owners** on days 0, 3, 7 and the day before the grace period ends
    (each step once), with the days left. After `COSTLENS_PAYMENT_GRACE_DAYS` (default
    14) the subscription ends: the free plan applies, nothing is deleted, owners get an
    email and admins an inbox notification.
- Settings → Plan explains a past-due subscription in plain words.
- `core.inbox.queue_email` is now the shared helper for one-off emails.
- Tests: `test_dunning.py` (reminder days, the full sequence to the free plan, owners
  only, recovery stops reminders); subscription tests updated.
- Checks: `make check` green (SQLite 784 passed / 18 skipped; agent 30; SDKs 5 + 5;
  frontend 161). `make test-pg` 802 passed.

### 2026-11-10 — Session 9cj: D.5 Kubernetes manifests (and a Compose fix)
- **Bug fixed in `compose.yaml`:** web and worker each had their own anonymous `/data`
  volume, so a cost file uploaded through web was not visible to the worker that
  imports it. They now share the `appdata` volume.
- `deploy/kubernetes` (kustomize): namespace with the restricted pod-security level;
  migration Job (owner role); web Deployment (2 replicas, readiness/liveness/startup
  probes on `/readyz` and `/healthz`, Service, PodDisruptionBudget, example Ingress);
  worker Deployment; default-deny ingress network policy with only web's port open; a
  `ReadWriteMany` data volume shared by web and worker; settings from a Secret generated
  from `costlens.env` (git-ignored; `costlens.env.example` provided). PostgreSQL stays
  external (managed or an operator).
- Every pod: non-root uid 10001, read-only root filesystem (an emptyDir for `/tmp`), no
  capabilities, no privilege escalation, no service-account token, resource limits.
  Checked: `kubectl kustomize` renders 11 objects; the image starts and answers
  `/healthz` and `/readyz` with a read-only root and all capabilities dropped.
- `docs/installation.md` gains a Kubernetes section.
- Tests: `test_k8s_manifests.py` (every manifest listed, pinned tag, hardened pods,
  shared volume, no settings file committed).
- Checks: `make check` green (SQLite 788 passed / 18 skipped). Not tested on a real
  cluster (none available here).

### 2026-11-10 — Session 9ck: D.7 pinned GitHub Actions
- All 25 `uses:` in `ci.yml` and `release.yml` (10 actions) are pinned to the full commit
  SHA of the tag they used, looked up with `git ls-remote`, with the version kept as a
  comment (`actions/checkout@3d3c42e… # v7.0.1`).
- `.github/dependabot.yml` (free): weekly updates for GitHub Actions (grouped), pip
  (`backend`), npm (`frontend`) and the Dockerfile base images.
- Tests: `test_ci_pins.py` fails if a workflow uses a tag or branch instead of a SHA.

### 2026-11-10 — Session 9cl: D.8 backup and restore
- `costlens backup create [FILE]`, `costlens backup verify FILE`,
  `costlens restore FILE --yes` (`backup.py`). One `.tar.gz` (created mode 0600) with a
  manifest (format, version, database kind, schema revision), the database (SQLite
  online copy, or `pg_dump -Fc` through the owner connection) and `<data dir>/files`.
  Encryption keys are deliberately not included; every backup and the CLI say so.
- Restore: verifies first (manifest, database readable, no unsafe paths, not from a newer
  CostLens, same database kind), writes a safety backup `pre-restore-<time>.tar.gz`,
  replaces the database (PostgreSQL: recreates the `public` schema, because
  `pg_restore --clean` cannot drop partitioned tables' constraints), keeps the old files
  folder as `files.before-restore-<time>`, then migrates (upgrades older backups,
  re-grants the runtime role).
- The image gains `postgresql-client-17` (verified later in session 9cp).
- `docs/backup.md`: what is not backed up, daily schedule with retention, off-site
  encrypted copies, Compose cron and Kubernetes CronJob, restore steps, quarterly
  restore drill.
- Tests: `test_backup.py` (round trip on SQLite and on PostgreSQL with `pg_dump`
  18 against server 17, safety copy, newer/unsafe/wrong-kind backups refused, CLI).
- Fixed a flaky test: the licence issuer's `verify --public-key KEY` failed for keys
  starting with "-" (1 in 64); usage is now `--public-key=KEY`.

### 2026-11-10 — Session 9cm: D.9 error tracking and uptime monitoring
- `sentry-sdk==2.74.0` (MIT; named in this item) in `core/error_tracking.py`, started by
  web and worker only when `COSTLENS_ERROR_TRACKING_DSN` is set (e.g. self-hosted
  GlitchTip). Scrubbed: no request bodies, cookies, headers, query strings, user, IP,
  local variables or source lines; messages, exceptions and breadcrumbs redacted like the
  logs; no tracing. Tested end to end with the real SDK client and a capturing transport.
- Worker heartbeat (also part of 0.4): job `system.heartbeat` every 5 minutes checks the
  database and calls `COSTLENS_HEARTBEAT_URL` (an Uptime Kuma push monitor).
- `deploy/monitoring/compose.yaml`: Uptime Kuma 2.5.9 (MIT), to run outside the CostLens
  hosting. `docs/monitoring.md`: GlitchTip setup (its official install guide; not
  hand-written here), the monitors to add (`/healthz`, `/readyz`, TLS expiry, heartbeat,
  metrics), alerts, and the public status page (completes B.13 once hosted).
- Where these run and their cost: Q7.
- Checks: `make check` green (SQLite 796 passed / 18 skipped; agent 30; SDKs 5 + 5;
  frontend 161; notices regenerated with sentry-sdk). `make test-pg` 814 passed.

### 2026-11-10 — Session 9cn: 2.16 consolidated view of all clouds and services
- `provider` is now a dimension: "Cloud or service" in the Costs page's Group by, the
  daily series and the report builder (it is in the daily rollup, so it stays fast).
- `GET /api/v1/costs/providers`: every source side by side in the display currency
  (each row converted per day, like the rest of the page): this month, the same days
  last month and change, last month, share, data freshness, and its connections with
  their status. Connected sources with no costs yet are listed too, so a broken
  connection is visible. Scoped custom roles are refused (unscoped totals).
- UI: an "All clouds and services" card on Costs when there are two or more sources,
  with share meters and a "Connection problem" link to the cloud settings.
- D.9 follow-up: removed the SDK's deprecated `send_default_pii` option (off by default;
  the scrubber and its end-to-end test still enforce it).
- Tests: `test_providers_view.py` (three providers in two currencies converted to USD,
  ordering, share, a connected source without costs, provider grouping in the daily
  series and the explorer, scoped roles refused) and a frontend test.
- Checks: `make check` green (SQLite 799 passed / 18 skipped; agent 30; SDKs 5 + 5;
  frontend 162). `make test-pg` 817 passed.

### 2026-11-10 — Session 9co: 0.4 operations polish
- Worker health: the `system.heartbeat` job (every 5 minutes) now always checks the
  database and records the time in the container; `costlens worker-health
  [--max-age 900]` exits 0 only after a recent beat. Used by the worker's Compose
  healthcheck (start period 10 minutes) and a Kubernetes liveness probe.
- Compose smoke test in CI (`image` job): generates a `.env`, brings up migrate, web,
  worker and PostgreSQL from the image just built, waits for `/readyz`, checks the worker
  runs, and takes and verifies a real `costlens backup` inside the container (which also
  proves `pg_dump` is in the image). Run locally in session 9cp, where it found a
  scheduler bug.
- Migration safety: `test_migration_safety.py` fails when an `upgrade()` drops or
  retypes anything or deletes rows without a `# destructive-ok: <reason>` comment. Only
  0051 needed one (it widens `account_id`); the migration recipe says to warn
  self-hosted customers in the release notes.
- Image slimming split out as **0.5**: it needs repeated local image builds.
- Checks: `make check` green (SQLite 801 passed / 18 skipped; agent 30; SDKs 5 + 5;
  frontend 162). `make test-pg` 819 passed.

### 2026-11-10 — Session 9cp: 0.5 smaller image, and a scheduler bug found by the smoke test
- **Critical bug fixed:** Procrastinate calls scheduled jobs with a `timestamp`
  argument that none of our 34 scheduled job functions accepted, so in a real
  PostgreSQL deployment **every scheduled job failed** (imports, detectors, alerts,
  budgets, retention, billing, webhooks). Tests never saw it because the in-process
  runner does not pass that argument. `jobs/runner.periodic_safe` now wraps scheduled
  jobs (sync and async); `test_jobs.py` registers every job through a fake Procrastinate
  app and checks each scheduled one accepts `timestamp`. Found by running the new
  Compose smoke test locally; afterwards every scheduled job ended with status Success.
- **Backups in Compose run through the `migrate` service** (it has the owner connection;
  web and worker use the runtime role, which by design cannot read the partition
  tables). Docs and the CI smoke test updated; a permission error now says so. The CI
  smoke test also fails if the worker log shows any failed job.
- Image: 1.24 GB → **839 MB** (−32%):
  - the `kubernetes` client (124 MB, 14 packages with its dependencies) moved from the
    backend's runtime dependencies to its dev extras: only the agent (its own image)
    uses it; notices regenerated (59 Python packages);
  - the wheels are installed from a build bind mount instead of being copied into a
    110 MB layer that was deleted later (which did not shrink the image).
  - Kept on purpose: pyarrow (Parquet billing exports), compiled bytecode (the root
    filesystem is read-only in Kubernetes, so it cannot be written at start-up).
- Verified locally with Docker this time (Docker Hub reachable again): `pg_dump` 17.11
  is in the image (D.8), the full Compose stack comes up with db, web and worker all
  **healthy** (worker via `costlens worker-health`, 0.4), and a PostgreSQL backup is
  created and verified inside the stack. The temporary `.env` used was deleted.
- Checks: `make check` green (SQLite 802 passed / 18 skipped; agent 30; SDKs 5 + 5;
  frontend 162). `make test-pg` 820 passed.
