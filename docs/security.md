# Security guide for operators

What protects an installation, and what you must look after. The customer-facing
summary is on the website's Security page.

## Keys you hold

| Key | Setting | If lost | If leaked |
|---|---|---|---|
| Session/CSRF signing key | `COSTLENS_SECRET_KEY` | Everyone signs in again | Rotate it; all sessions end |
| Encryption keys (Fernet) | `COSTLENS_ENCRYPTION_KEYS` | Stored cloud credentials, webhook addresses and SSO secrets cannot be read; reconnect them | Add a new key first in the list, run `costlens rotate-keys`, then remove the old key |
| Database passwords | `POSTGRES_PASSWORD`, `COSTLENS_DB_APP_PASSWORD` | Reset in PostgreSQL | Change them in PostgreSQL and `.env` |

Keep them in a secrets manager or an `.env` file readable only by the service account.
Back up the encryption keys separately from database backups.

## Database roles and tenant isolation

Use two roles, as `compose.yaml` does: the owner role runs migrations only; the runtime
role (`COSTLENS_DATABASE_URL`) has no superuser or `BYPASSRLS`, so PostgreSQL row-level
security separates organisations. CostLens refuses to start with a privileged runtime role
unless `COSTLENS_ALLOW_PRIVILEGED_DB_ROLE=true` (not recommended).

## Cloud access

- AWS: customers create a read-only role from a template; it trusts only
  `COSTLENS_AWS_PRINCIPAL_ARN` and requires a per-connection external id.
- Azure and Google Cloud: service principals and service accounts with reader roles; their
  secrets are encrypted at rest.
- Automatic fixes need a separate role the customer creates, owner approval for each
  change and every safety check passing.

## Network

- Put TLS in front of the web port; set `COSTLENS_BASE_URL` to the HTTPS address so
  cookies are marked secure.
- Outgoing calls (Slack, Teams, outgoing webhooks, SSO discovery) go through an SSRF guard
  that blocks private addresses. Webhook targets must be public HTTPS addresses.
- `/metrics` needs `COSTLENS_METRICS_TOKEN` outside local mode.

## Sign-in

Use SSO (Google or any OpenID Connect provider); local email sign-in exists only in local
mode. Organisations can enforce SSO for their verified domains (owners keep a
break-glass sign-in) and provision people with SCIM. `COSTLENS_ALLOWED_EMAIL_DOMAINS`
limits who can sign in to an installation.

## Rate limits

Sign-in, anonymous, per-user/API-key and write limits are on by default (see the
`COSTLENS_RATE_LIMIT_*` settings). Responses carry `RateLimit-*` headers and `429` with
`Retry-After` when exceeded.

## Logs and audit

Logs are JSON lines with a request id; secrets and webhook addresses are redacted. Each
organisation has an audit log of changes, kept for its retention setting (default 365
days) and exportable as CSV.

## Licences (self-hosted)

Licence keys are verified offline with Yugiverse's built-in public keys. No usage data is
sent anywhere. The signing private key never leaves Yugiverse's issuing machine.

## Reporting a vulnerability

[SECURITY CONTACT — tracker Q6].
