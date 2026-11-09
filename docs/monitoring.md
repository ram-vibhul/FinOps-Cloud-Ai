# Monitoring the hosted service

Two self-hosted, open-source tools, no paid service by default. Where they run (and the
small hosting cost) is tracker Q7. Self-hosted customers can use the same setup.

## Error tracking: GlitchTip (MIT)

CostLens reports unhandled errors with the Sentry protocol when
`COSTLENS_ERROR_TRACKING_DSN` is set (web and worker). It is **off** otherwise.

- Install GlitchTip with its official guide (glitchtip.com, "Install"): it needs
  PostgreSQL and runs as a web and a worker container. Run it outside the CostLens
  hosting so errors still arrive when CostLens has problems.
- Create a project of type Python, copy its DSN into `COSTLENS_ERROR_TRACKING_DSN`, and
  set `COSTLENS_ENVIRONMENT_NAME` (e.g. `production`, `staging`).
- What is sent: the error type, a redacted message and the stack (file, function, line).
  What is never sent: request bodies, cookies, headers, query strings, user details,
  IP addresses, local variables or source lines. Messages and breadcrumbs go through the
  same redaction as the logs (secrets, emails, phone numbers, IP addresses). No
  performance tracing.
- Alerts: in GlitchTip, alert the on-call email (or a Slack/Teams webhook) on new issues.

## Uptime checks and status page: Uptime Kuma (MIT)

`deploy/monitoring/compose.yaml` runs Uptime Kuma. Add these monitors:

| Monitor | Type | Target | Every | Alert after |
|---|---|---|---|---|
| Web up | HTTP(s) | `https://<host>/healthz` (expects `{"status":"ok"}`) | 60 s | 2 failures |
| Ready (database reachable) | HTTP(s) | `https://<host>/readyz` | 60 s | 2 failures |
| TLS certificate | HTTP(s) with certificate expiry notification | `https://<host>/` | daily | 14 days before expiry |
| Background jobs | Push | `COSTLENS_HEARTBEAT_URL` (see below) | 5 min | 2 missed |
| Metrics scrape | HTTP(s) with `Authorization: Bearer <COSTLENS_METRICS_TOKEN>` | `https://<host>/metrics` | 5 min | 3 failures |

Background jobs: create a *Push* monitor in Uptime Kuma and put its push URL in
`COSTLENS_HEARTBEAT_URL`. The worker checks the database and calls it every 5 minutes
(job `system.heartbeat`); a missed beat means jobs have stopped.

Notifications: Uptime Kuma supports email, Slack, Microsoft Teams and many others;
send to the on-call person.

**Status page (B.13):** in Uptime Kuma, create a status page with "Web up" and "Ready",
publish it at e.g. `status.<domain>`, and link it from the website's Support page.
