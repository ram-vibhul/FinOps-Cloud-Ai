# Security guide for operators

What protects an installation, and what you must look after. The customer-facing
summary is on the website's Security page.

## Keys you hold

| Key | Setting | If lost | If leaked |
|---|---|---|---|
| Session/CSRF signing key | `CCRADAR_SECRET_KEY` | Everyone signs in again | Rotate it; all sessions end |
| Encryption keys (Fernet) | `CCRADAR_ENCRYPTION_KEYS` | Stored cloud credentials, webhook addresses and SSO secrets cannot be read; reconnect them | Add a new key first in the list, run `ccradar rotate-keys`, then remove the old key |
| Database passwords | `POSTGRES_PASSWORD`, `CCRADAR_DB_APP_PASSWORD` | Reset in PostgreSQL | Change them in PostgreSQL and `.env` |

Keep them in a secrets manager or an `.env` file readable only by the service account.
Back up the encryption keys separately from database backups.

## Database roles and tenant isolation

Use two roles, as `compose.yaml` does: the owner role runs migrations only; the runtime
role (`CCRADAR_DATABASE_URL`) has no superuser or `BYPASSRLS`, so PostgreSQL row-level
security separates organisations. CloudCostRadar refuses to start with a privileged runtime role
unless `CCRADAR_ALLOW_PRIVILEGED_DB_ROLE=true` (not recommended).

## Cloud access

- AWS: customers create a read-only role from a template; it trusts only
  `CCRADAR_AWS_PRINCIPAL_ARN` and requires a per-connection external id.
- Azure and Google Cloud: service principals and service accounts with reader roles; their
  secrets are encrypted at rest.
- Automatic fixes need a separate role the customer creates, owner approval for each
  change and every safety check passing.
- Office-hours schedules (stop and start non-production servers) use the same fixes
  role and need an administrator's approval for each schedule. Only servers
  CloudCostRadar stopped are started again, and servers tagged `ccradar-protect` or in an
  Auto Scaling group are never touched. The role's template gained `ec2:StartInstances`
  for this. A role created from an older template can still stop servers, but starting
  them is refused by AWS and shows as an error on the schedule until the stack is
  updated.
- Radar's "How to undo" shows AWS CLI commands for a person to run; CloudCostRadar never
  runs them.

## Network

- Put TLS in front of the web port; set `CCRADAR_BASE_URL` to the HTTPS address so
  cookies are marked secure.
- Outgoing calls (Slack, Teams, outgoing webhooks, SSO discovery) go through an SSRF guard
  that blocks private addresses. Webhook targets must be public HTTPS addresses.
- `/metrics` needs `CCRADAR_METRICS_TOKEN` outside local mode.

## Sign-in

Use SSO (Google or any OpenID Connect provider); local email sign-in exists only in local
mode. Organisations can enforce SSO for their verified domains (owners keep a
break-glass sign-in) and provision people with SCIM. `CCRADAR_ALLOWED_EMAIL_DOMAINS`
limits who can sign in to an installation.

## Rate limits

Sign-in, anonymous, per-user/API-key and write limits are on by default (see the
`CCRADAR_RATE_LIMIT_*` settings). Responses carry `RateLimit-*` headers and `429` with
`Retry-After` when exceeded.

## Logs and audit

Logs are JSON lines with a request id; secrets and webhook addresses are redacted. Each
organisation has an audit log of changes, kept for its retention setting (default 365
days) and exportable as CSV.

### Streaming the audit log to a SIEM

Admins can stream the audit log (Settings → Audit) to Splunk (HTTP Event Collector),
Microsoft Sentinel (Azure Monitor Logs Ingestion API), Datadog Logs, a syslog server over
TLS (RFC 5424/5425, port 6514 by default) or any HTTPS webhook. Business and Enterprise
plans include it.

- **Delivery.** A job sends new events every minute, in batches of up to 500. Each stream
  keeps its place in the log and moves on only after the receiver accepts a batch. If the
  receiver is down, it retries after 1 minute, 5 minutes, 30 minutes, 2 hours, then every
  6 hours, starting from the same event, so nothing is skipped. Events newer than two
  minutes wait for the next run, so a change that commits late is not missed. A new
  stream starts with new events, or with everything still kept if you choose back-fill.
- **Signing.** Every HTTP request carries
  `X-CloudCostRadar-Signature: t=<unix seconds>,v1=<hex>`. The hex is HMAC-SHA256, with
  the stream's signing secret, over `"<t>.<raw body>"` (the same scheme as outgoing
  webhooks). Syslog messages carry the HMAC of their JSON body as
  `[ccradar@32473 sig="<hex>"]`. The signing secret is shown once and can be rotated.
- **Secrets.** Tokens, keys and client secrets are encrypted at rest and never shown
  again.
- **Addresses.** Only public internet addresses are allowed. They are checked when you
  save a stream and again before every send, and redirects are not followed.
- **Sentinel.** Uses an Entra app (client credentials) with the Monitoring Metrics
  Publisher role on the data collection rule. The older HTTP Data Collector API is not
  used because Microsoft retired it in September 2026.

SOC 2 and ISO 27001 certification need a paid audit and have not been done.

## Peer benchmarks

Off by default. When an admin opts in, four ratios for the last full month are shared:
effective savings rate, waste share, share of spend with a team, and EC2 cost per
vCPU-hour, plus a size band (small, medium or large). No amounts, names, accounts,
resources or tags. Other organisations see only percentiles, and only from at least
five organisations. Opting out deletes the shared ratios at once. Demo organisations
never take part.

## Licences (self-hosted)

Licence keys are verified offline with Yugiverse's built-in public keys. No usage data is
sent anywhere. The signing private key never leaves Yugiverse's issuing machine.

## Reporting a vulnerability

[SECURITY CONTACT — tracker Q6].
